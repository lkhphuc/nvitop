# This file is part of nvitop, the interactive NVIDIA-GPU process viewer.
# License: GNU GPL version 3.

# pylint: disable=missing-module-docstring,missing-class-docstring,missing-function-docstring
# pylint: disable=invalid-name,no-member

import datetime
import functools
import os
import threading
import time
from abc import ABCMeta

from cachetools.func import ttl_cache

from . import host
from .utils import Snapshot, bytes2human, timedelta2human


if host.POSIX:
    def add_quotes(s):
        if s == '':
            return '""'
        if '$' not in s and '\\' not in s:
            if ' ' not in s:
                return s
            if '"' not in s:
                return '"{}"'.format(s)
        if "'" not in s:
            return "'{}'".format(s)
        return '"{}"'.format(s.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$'))
elif host.WINDOWS:
    def add_quotes(s):
        if s == '':
            return '""'
        if '%' not in s and '^' not in s:
            if ' ' not in s and '\\' not in s:
                return s
            if '"' not in s:
                return '"{}"'.format(s)
        return '"{}"'.format(s.replace('^', '^^').replace('"', '^"').replace('%', '^%'))
else:
    def add_quotes(s):
        return '"{}"'.format(s)


def auto_garbage_clean(default=None):
    def wrapper(func):
        @functools.wraps(func)
        def wrapped(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except host.PsutilError:
                try:
                    with GpuProcess.INSTANCE_LOCK:
                        del GpuProcess.INSTANCES[(self.pid, self.device)]
                except KeyError:
                    pass
                try:
                    with HostProcess.INSTANCE_LOCK:
                        del HostProcess.INSTANCES[self.pid]
                except KeyError:
                    pass
                return default
        return wrapped

    return wrapper


class HostProcess(host.Process, metaclass=ABCMeta):
    INSTANCE_LOCK = threading.RLock()
    INSTANCES = {}

    def __new__(cls, pid=None):
        if pid is None:
            pid = os.getpid()

        try:
            return cls.INSTANCES[pid]
        except KeyError:
            pass

        instance = super().__new__(cls)
        instance.__init__(pid)
        with cls.INSTANCE_LOCK:
            cls.INSTANCES[pid] = instance
        return instance

    def __init__(self, pid=None):  # pylint: disable=super-init-not-called
        super()._init(pid, True)
        try:
            super().cpu_percent()
        except host.PsutilError:
            pass

    def __str__(self):
        return super().__str__().replace(self.__class__.__module__ + '.', '', 1)

    __repr__ = __str__

    cpu_percent = ttl_cache(ttl=1.0)(host.Process.cpu_percent)

    memory_percent = ttl_cache(ttl=1.0)(host.Process.memory_percent)

    if host.WINDOWS:
        def username(self):
            return super().username().split('\\')[-1]

    def command(self):
        cmdline = self.cmdline()
        if len(cmdline) > 1:
            cmdline = '\0'.join(cmdline).strip('\0').split('\0')
        if len(cmdline) == 1:
            return cmdline[0]
        return ' '.join(map(add_quotes, cmdline))

    def as_snapshot(self):
        return Snapshot(real=self, **self.as_dict())


class GpuProcess(object):
    CLI_MODE = False
    INSTANCE_LOCK = threading.RLock()
    INSTANCES = {}
    SNAPSHOT_LOCK = threading.RLock()
    HOST_SNAPSHOTS = {}

    def __new__(cls, pid, device, gpu_memory=None, type=None):  # pylint: disable=redefined-builtin
        try:
            return cls.INSTANCES[(pid, device)]
        except KeyError:
            pass

        instance = super().__new__(cls)
        instance.__init__(pid, device, gpu_memory, type)
        with cls.INSTANCE_LOCK:
            cls.INSTANCES[(pid, device)] = instance
            with cls.SNAPSHOT_LOCK:
                try:
                    del cls.HOST_SNAPSHOTS[pid]
                except KeyError:
                    pass
        return instance

    def __init__(self, pid, device, gpu_memory=None, type=None):  # pylint: disable=redefined-builtin
        self._pid = pid
        self.host = HostProcess(pid)
        self._ident = (*self.host._ident, device.index)

        self.device = device
        if gpu_memory is None and not hasattr(self, '_gpu_memory'):
            gpu_memory = 'N/A'
        if gpu_memory is not None:
            self.set_gpu_memory(gpu_memory)
        if type is None and not hasattr(self, '_type'):
            type = ''
        if type is not None:
            self.type = type
        self._hash = None
        self._username = None

    def __str__(self):
        return '{}(device={}, gpu_memory={}, host={})'.format(
            self.__class__.__name__,
            self.device, self.gpu_memory_human(), self.host
        )

    __repr__ = __str__

    def __eq__(self, other):
        if not isinstance(other, (GpuProcess, host.Process)):
            return NotImplemented
        return self._ident == other._ident

    def __ne__(self, other):
        return not self == other

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self._ident)
        return self._hash

    def __getattr__(self, name):
        try:
            return super().__getattr__(name)
        except AttributeError:
            return getattr(self.host, name)

    @property
    def pid(self):
        return self._pid

    def gpu_memory(self):
        return self._gpu_memory

    def gpu_memory_human(self):
        return self._gpu_memory_human

    def set_gpu_memory(self, value):
        self._gpu_memory = value  # pylint: disable=attribute-defined-outside-init
        self._gpu_memory_human = bytes2human(self.gpu_memory())  # pylint: disable=attribute-defined-outside-init

    def update_gpu_memory(self):
        self._gpu_memory = 'N/A'  # pylint: disable=attribute-defined-outside-init
        self._gpu_memory_human = 'N/A'  # pylint: disable=attribute-defined-outside-init
        self.device.processes()
        return self.gpu_memory()

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        self._type = ''
        if 'C' in value:
            self._type += 'C'
        if 'G' in value:
            self._type += 'G'
        if 'X' in value or self._type == 'CG':
            self._type = 'X'

    @ttl_cache(ttl=1.0)
    @auto_garbage_clean(default=datetime.timedelta())
    def running_time(self):
        return datetime.datetime.now() - datetime.datetime.fromtimestamp(self.create_time())

    def running_time_human(self):
        return timedelta2human(self.running_time())

    @auto_garbage_clean(default=time.time())
    def create_time(self):
        return self.host.create_time()

    @auto_garbage_clean(default='N/A')
    def username(self):
        if self._username is None:
            self._username = self.host.username()
        return self._username

    @auto_garbage_clean(default='N/A')
    def name(self):
        return self.host.name()

    @auto_garbage_clean(default=0.0)
    def cpu_percent(self):
        return self.host.cpu_percent()

    @auto_garbage_clean(default=0.0)
    def memory_percent(self):
        return self.host.memory_percent()

    @auto_garbage_clean(default=('No Such Process',))
    def cmdline(self):
        cmdline = self.host.cmdline()
        if len(cmdline) == 0:
            cmdline = ('Zombie Process',)
        return cmdline

    def command(self):
        return HostProcess.command(self)

    @classmethod
    def clear_host_snapshots(cls):
        with cls.SNAPSHOT_LOCK:
            cls.HOST_SNAPSHOTS.clear()

    @auto_garbage_clean(default=None)
    def as_snapshot(self):
        with self.SNAPSHOT_LOCK:
            try:
                host_snapshot = self.HOST_SNAPSHOTS[self.pid]
            except KeyError:
                with self.host.oneshot():
                    host_snapshot = Snapshot(
                        real=self.host,
                        is_running=self.host.is_running(),
                        status=self.host.status(),
                        username=self.username(),
                        name=self.name(),
                        cmdline=self.cmdline(),
                        cpu_percent=self.cpu_percent(),
                        memory_percent=self.memory_percent(),
                        running_time=self.running_time()
                    )

                if host_snapshot.cpu_percent < 1000.0:
                    host_snapshot.cpu_percent_string = '{:.1f}%'.format(host_snapshot.cpu_percent)
                elif host_snapshot.cpu_percent < 10000:
                    host_snapshot.cpu_percent_string = '{}%'.format(int(host_snapshot.cpu_percent))
                else:
                    host_snapshot.cpu_percent_string = '9999+%'
                host_snapshot.memory_percent_string = '{:.1f}%'.format(host_snapshot.memory_percent)

                if host_snapshot.is_running:
                    host_snapshot.running_time_human = timedelta2human(host_snapshot.running_time)
                else:
                    host_snapshot.running_time_human = 'N/A'
                    host_snapshot.cmdline = ('No Such Process',)
                if len(host_snapshot.cmdline) > 1:
                    host_snapshot.cmdline = '\0'.join(host_snapshot.cmdline).strip('\0').split('\0')
                if len(host_snapshot.cmdline) == 1:
                    host_snapshot.command = host_snapshot.cmdline[0]
                else:
                    host_snapshot.command = ' '.join(map(add_quotes, host_snapshot.cmdline))

                if self.CLI_MODE:
                    self.HOST_SNAPSHOTS[self.pid] = host_snapshot

        return Snapshot(
            real=self,
            identity=self._ident,
            pid=self.pid,
            device=self.device,
            gpu_memory=self.gpu_memory(),
            gpu_memory_human=self.gpu_memory_human(),
            type=self.type,
            username=host_snapshot.username,
            name=host_snapshot.name,
            cmdline=host_snapshot.cmdline,
            command=host_snapshot.command,
            cpu_percent=host_snapshot.cpu_percent,
            cpu_percent_string=host_snapshot.cpu_percent_string,
            memory_percent=host_snapshot.memory_percent,
            memory_percent_string=host_snapshot.memory_percent_string,
            is_running=host_snapshot.is_running,
            running_time=host_snapshot.running_time,
            running_time_human=host_snapshot.running_time_human
        )


HostProcess.register(GpuProcess)