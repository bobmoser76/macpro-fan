#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Robert Moser

from __future__ import annotations

import configparser
import json
import os
import signal
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

CONFIG = Path('/etc/macpro-fan.conf')
STATE = Path('/run/macpro-fan/state.json')
HISTORY = Path('/var/lib/macpro-fan/history.json')
SMC = Path('/sys/devices/platform/applesmc.768')
FAN_MANUAL = SMC / 'fan1_manual'
FAN_OUTPUT = SMC / 'fan1_output'
FAN_INPUT = SMC / 'fan1_input'
FAN_MIN = SMC / 'fan1_min'
FAN_MAX = SMC / 'fan1_max'
HWMON = Path('/sys/class/hwmon')

@dataclass(frozen=True)
class Point:
    temp: float
    rpm: int

@dataclass
class Profile:
    name: str
    curve: list[Point]
    gpu_bias: float
    gpu_floor_temp: float
    gpu_floor_rpm: int
    emergency_temp: float
    poll_interval: float
    idle_poll_interval: float
    idle_after_seconds: float
    average_samples: int
    max_rpm_increase: int
    max_rpm_decrease: int
    log_temp_delta: float

@dataclass
class AutoConfig:
    idle_load_per_core: float
    performance_load_per_core: float
    idle_gpu_busy: int
    performance_gpu_busy: int
    idle_hold_seconds: float
    performance_hold_seconds: float


def read_int(path: Path) -> int:
    return int(path.read_text().strip())


def write_int(path: Path, value: int) -> None:
    path.write_text(f'{value}\n')


def hwmon_devices(name: str) -> list[Path]:
    found = []
    for dev in sorted(HWMON.glob('hwmon*')):
        try:
            if (dev / 'name').read_text().strip() == name:
                found.append(dev.resolve())
        except OSError:
            pass
    return found


def temp_by_label(device: Path, wanted: str) -> float | None:
    for label in device.glob('temp*_label'):
        try:
            if label.read_text().strip().lower() == wanted.lower():
                inp = label.with_name(label.name.replace('_label', '_input'))
                return read_int(inp) / 1000.0
        except OSError:
            pass
    return None


def read_cpu() -> float:
    for dev in hwmon_devices('coretemp'):
        value = temp_by_label(dev, 'Package id 0')
        if value is not None:
            return value
    raise RuntimeError('CPU package temperature sensor not found')


def read_gpus() -> list[dict]:
    gpus = []
    for dev in hwmon_devices('amdgpu'):
        temp = temp_by_label(dev, 'edge')
        if temp is None and (dev / 'temp1_input').exists():
            temp = read_int(dev / 'temp1_input') / 1000.0
        if temp is None:
            continue
        busy = None
        busy_path = dev / 'device' / 'gpu_busy_percent'
        if busy_path.exists():
            try:
                busy = read_int(busy_path)
            except OSError:
                pass
        gpus.append({'path': str(dev), 'temp': temp, 'busy': busy})
    if len(gpus) != 2:
        raise RuntimeError(f'Expected 2 AMD GPU sensors, found {len(gpus)}')
    return gpus


def parse_curve(value: str) -> list[Point]:
    points = []
    for item in value.split(','):
        temp, rpm = item.strip().split(':', 1)
        points.append(Point(float(temp), int(rpm)))
    points.sort(key=lambda p: p.temp)
    if len(points) < 2:
        raise ValueError('Fan curve requires at least two points')
    return points


def load_config() -> tuple[str, dict[str, Profile], AutoConfig]:
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG):
        raise RuntimeError(f'Cannot read {CONFIG}')
    selected = cfg.get('General', 'Profile', fallback='Auto').strip()
    profiles: dict[str, Profile] = {}
    for section in cfg.sections():
        if not section.startswith('Profile:'):
            continue
        name = section.split(':', 1)[1]
        p = cfg[section]
        profiles[name] = Profile(
            name=name,
            curve=parse_curve(p['Curve']),
            gpu_bias=p.getfloat('GPUBias', 3.0),
            gpu_floor_temp=p.getfloat('GPUFloorTemp', 60.0),
            gpu_floor_rpm=p.getint('GPUFloorRPM', 1200),
            emergency_temp=p.getfloat('EmergencyTemp', 85.0),
            poll_interval=p.getfloat('PollInterval', 2.0),
            idle_poll_interval=p.getfloat('IdlePollInterval', 5.0),
            idle_after_seconds=p.getfloat('IdleAfterSeconds', 180.0),
            average_samples=p.getint('AverageSamples', 4),
            max_rpm_increase=p.getint('MaxRPMIncrease', 150),
            max_rpm_decrease=p.getint('MaxRPMDecrease', 75),
            log_temp_delta=p.getfloat('LogTempDelta', 1.0),
        )
    if not profiles:
        raise RuntimeError('No profiles found')
    a = cfg['Auto'] if 'Auto' in cfg else {}
    auto = AutoConfig(
        idle_load_per_core=float(a.get('IdleLoadPerCore', 0.20)),
        performance_load_per_core=float(a.get('PerformanceLoadPerCore', 0.70)),
        idle_gpu_busy=int(a.get('IdleGPUBusy', 10)),
        performance_gpu_busy=int(a.get('PerformanceGPUBusy', 60)),
        idle_hold_seconds=float(a.get('IdleHoldSeconds', 120)),
        performance_hold_seconds=float(a.get('PerformanceHoldSeconds', 15)),
    )
    if selected.lower() != 'auto' and selected not in profiles:
        raise RuntimeError(f"Unknown profile '{selected}'")
    return selected, profiles, auto


def load_average_per_core() -> float:
    try:
        load1 = float(Path('/proc/loadavg').read_text().split()[0])
        return load1 / max(1, os.cpu_count() or 1)
    except Exception:
        return 0.0


class History:
    def __init__(self) -> None:
        self.data = {
            'started': time.time(), 'samples': 0, 'cpu_peak': 0.0,
            'gpu0_peak': 0.0, 'gpu1_peak': 0.0, 'fan_peak': 0,
            'fan_sum': 0, 'emergency_events': 0,
        }
        try:
            self.data.update(json.loads(HISTORY.read_text()))
        except Exception:
            pass

    def update(self, cpu: float, gpu0: float, gpu1: float, fan: int, emergency_entered: bool) -> None:
        self.data['samples'] += 1
        self.data['cpu_peak'] = max(self.data['cpu_peak'], cpu)
        self.data['gpu0_peak'] = max(self.data['gpu0_peak'], gpu0)
        self.data['gpu1_peak'] = max(self.data['gpu1_peak'], gpu1)
        self.data['fan_peak'] = max(self.data['fan_peak'], fan)
        self.data['fan_sum'] += fan
        if emergency_entered:
            self.data['emergency_events'] += 1

    def save(self) -> None:
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data))
        os.replace(tmp, HISTORY)


class Controller:
    def __init__(self) -> None:
        self.running = True
        self.selected, self.profiles, self.auto = load_config()
        self.profile = self.profiles.get('Balanced') or next(iter(self.profiles.values()))
        if self.selected.lower() != 'auto':
            self.profile = self.profiles[self.selected]
        self.auto_profile = self.profile.name
        self.config_mtime = CONFIG.stat().st_mtime
        self.window = deque(maxlen=self.profile.average_samples)
        self.last_temp = None
        self.last_target = None
        self.last_mode = None
        self.idle_since = None
        self.performance_since = None
        self.history = History()
        self.last_history_save = 0.0
        self.was_emergency = False

    def validate(self) -> None:
        missing = [str(p) for p in (FAN_MANUAL, FAN_OUTPUT, FAN_INPUT, FAN_MIN, FAN_MAX) if not p.exists()]
        if missing:
            raise RuntimeError('Missing SMC interfaces: ' + ', '.join(missing))
        cpu = read_cpu()
        gpus = read_gpus()
        print(f"Sensor check passed: CPU={cpu:.1f}C GPU0={gpus[0]['temp']:.1f}C GPU1={gpus[1]['temp']:.1f}C")

    def stop(self, *_args) -> None:
        self.running = False

    def restore(self) -> None:
        try:
            write_int(FAN_MANUAL, 0)
            print('Returned fan control to Apple SMC', flush=True)
        except Exception as exc:
            print(f'WARNING: failed to restore SMC control: {exc}', flush=True)

    def reload_if_changed(self) -> None:
        try:
            mtime = CONFIG.stat().st_mtime
        except OSError:
            return
        if mtime == self.config_mtime:
            return
        self.selected, self.profiles, self.auto = load_config()
        if self.selected.lower() != 'auto':
            self.profile = self.profiles[self.selected]
            self.auto_profile = self.profile.name
        self.window = deque(maxlen=self.profile.average_samples)
        self.config_mtime = mtime
        print(f'Reloaded configuration; selected={self.selected}', flush=True)

    def choose_auto_profile(self, load_per_core: float, gpu_busy: int, hottest: float) -> None:
        now = time.monotonic()
        high = load_per_core >= self.auto.performance_load_per_core or gpu_busy >= self.auto.performance_gpu_busy or hottest >= 68.0
        idle = load_per_core <= self.auto.idle_load_per_core and gpu_busy <= self.auto.idle_gpu_busy and hottest <= 52.0
        if high:
            self.performance_since = self.performance_since or now
            self.idle_since = None
            if now - self.performance_since >= self.auto.performance_hold_seconds:
                self.auto_profile = 'Performance'
        elif idle:
            self.idle_since = self.idle_since or now
            self.performance_since = None
            if now - self.idle_since >= self.auto.idle_hold_seconds:
                self.auto_profile = 'Silent'
        else:
            self.idle_since = None
            self.performance_since = None
            self.auto_profile = 'Balanced'
        chosen = self.profiles.get(self.auto_profile) or self.profiles.get('Balanced') or next(iter(self.profiles.values()))
        if chosen.name != self.profile.name:
            self.profile = chosen
            self.window = deque(maxlen=self.profile.average_samples)
            print(f'Auto profile switched to {self.profile.name}', flush=True)

    def target_for_temp(self, temp: float, min_rpm: int, max_rpm: int) -> int:
        curve = self.profile.curve
        if temp <= curve[0].temp:
            return max(min_rpm, curve[0].rpm)
        for low, high in zip(curve, curve[1:]):
            if temp <= high.temp:
                ratio = (temp - low.temp) / (high.temp - low.temp)
                rpm = round(low.rpm + ratio * (high.rpm - low.rpm))
                return max(min_rpm, min(max_rpm, rpm))
        return max_rpm

    def rate_limit(self, current: int, requested: int) -> int:
        if requested > current:
            return min(requested, current + self.profile.max_rpm_increase)
        return max(requested, current - self.profile.max_rpm_decrease)

    def save_state(self, data: dict) -> None:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data))
        os.replace(tmp, STATE)

    def should_log(self, temp: float, target: int, mode: str) -> bool:
        return self.last_temp is None or abs(temp - self.last_temp) >= self.profile.log_temp_delta or target != self.last_target or mode != self.last_mode

    def run(self) -> int:
        self.validate()
        min_rpm = read_int(FAN_MIN)
        max_rpm = read_int(FAN_MAX)
        target = max(min_rpm, min(max_rpm, read_int(FAN_INPUT)))
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        print(f'Starting selected={self.selected} initial_profile={self.profile.name} fan_range={min_rpm}-{max_rpm}RPM', flush=True)
        write_int(FAN_MANUAL, 1)
        try:
            while self.running:
                interval = self.profile.poll_interval
                try:
                    self.reload_if_changed()
                    cpu = read_cpu()
                    gpus = read_gpus()
                    gpu0, gpu1 = gpus[0]['temp'], gpus[1]['temp']
                    gpu_busy = max(g['busy'] or 0 for g in gpus)
                    load_per_core = load_average_per_core()
                    actual_hot = max(cpu, gpu0, gpu1)
                    if self.selected.lower() == 'auto':
                        self.choose_auto_profile(load_per_core, gpu_busy, actual_hot)
                    controls = {'CPU': cpu, 'GPU0': gpu0 + self.profile.gpu_bias, 'GPU1': gpu1 + self.profile.gpu_bias}
                    source = max(controls, key=controls.get)
                    self.window.append(controls[source])
                    control = sum(self.window) / len(self.window)
                    requested = self.target_for_temp(control, min_rpm, max_rpm)
                    mode = 'Normal'
                    if (gpu0 + gpu1) / 2.0 >= self.profile.gpu_floor_temp:
                        requested = max(requested, self.profile.gpu_floor_rpm)
                        mode = 'GPU floor'
                    emergency = actual_hot >= self.profile.emergency_temp
                    if emergency:
                        target = max_rpm
                        mode = 'Emergency'
                    else:
                        target = self.rate_limit(target, requested)
                    target = max(min_rpm, min(max_rpm, target))
                    write_int(FAN_OUTPUT, target)
                    actual_rpm = read_int(FAN_INPUT)
                    emergency_entered = emergency and not self.was_emergency
                    self.was_emergency = emergency
                    self.history.update(cpu, gpu0, gpu1, actual_rpm, emergency_entered)
                    now = time.time()
                    if now - self.last_history_save >= 30:
                        self.history.save()
                        self.last_history_save = now
                    self.save_state({
                        'timestamp': now, 'selected_profile': self.selected,
                        'active_profile': self.profile.name, 'cpu': cpu,
                        'gpu0': gpu0, 'gpu1': gpu1,
                        'gpu0_busy': gpus[0]['busy'], 'gpu1_busy': gpus[1]['busy'],
                        'load_per_core': load_per_core, 'control_temp': control,
                        'control_source': source, 'fan_rpm': actual_rpm,
                        'target_rpm': target, 'mode': mode,
                        'poll_interval': interval,
                    })
                    if self.should_log(control, target, mode):
                        print(
                            f'selected={self.selected} active={self.profile.name} '
                            f'CPU={cpu:.1f}C GPU0={gpu0:.1f}C GPU1={gpu1:.1f}C '
                            f'busy={gpu_busy}% load/core={load_per_core:.2f} '
                            f'control={control:.1f}C({source}) fan={actual_rpm}RPM '
                            f'target={target}RPM mode={mode}', flush=True)
                        self.last_temp, self.last_target, self.last_mode = control, target, mode
                except Exception as exc:
                    write_int(FAN_OUTPUT, max_rpm)
                    print(f'Sensor/control error; forcing maximum fan: {exc}', flush=True)
                    self.save_state({'timestamp': time.time(), 'selected_profile': self.selected, 'active_profile': self.profile.name, 'target_rpm': max_rpm, 'mode': 'Failsafe', 'error': str(exc)})
                time.sleep(interval)
        finally:
            self.history.save()
            self.restore()
            try:
                STATE.unlink(missing_ok=True)
            except OSError:
                pass
        return 0


def main() -> int:
    controller = Controller()
    if '--check' in sys.argv[1:]:
        controller.validate()
        print(f"Configuration selection '{controller.selected}' is valid")
        return 0
    return controller.run()

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'Fatal error: {exc}', flush=True)
        try:
            write_int(FAN_MANUAL, 0)
        except Exception:
            pass
        raise SystemExit(1)
