import functools
import sys
import argparse
import time

from prometheus_client import Gauge, start_http_server, Info

from nvitop import Device, colored, libnvml, GiB, host, __version__


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='NVITOP')
    parser.add_argument(
        '-p',
        '--port',
        type=int,
        default=8000,
        help='Port to listen on (default: %(default)d)',
    )
    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to listen on (default: %(default)s)',
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='store_true',
        help='Enable verbose output',
    )
    parser.add_argument(
        '-d',
        '--debug',
        action='store_true',
        help='Enable debug output',
    )
    parser.add_argument(
        '-c',
        '--config',
        type=str,
        default=None,
        help='Path to config file',
    )
    parser.add_argument(
        '-s',
        '--server',
        action='store_true',
        help='Start the server',
    )

    return parser.parse_args()


def main() -> int:
    args = parse_arguments()

    try:
        device_count = Device.count()
    except libnvml.NVMLError_LibraryNotFound:
        return 1
    except libnvml.NVMLError as ex:
        print(
            '{} {}'.format(colored('NVML ERROR:', color='red', attrs=('bold',)), ex),
            file=sys.stderr,
        )
        return 1

    info = Info(
        'nvitop',
        documentation='NVITOP',
    )
    info.info(
        {
            'device_count': str(device_count),
            'driver_version': Device.driver_version(),
        }
    )

    # Create a gauge for each metric
    host_cpu_percent = Gauge(
        name='host_cpu_percent',
        documentation='Host CPU percent',
        unit='Percentage',
    )
    host_memory_total = Gauge(
        name='host_memory_total',
        documentation='Host memory total',
        unit='GiB',
    )
    host_memory_used = Gauge(
        name='host_memory_used',
        documentation='Host memory used',
        unit='GiB',
    )
    host_memory_free = Gauge(
        name='host_memory_free',
        documentation='Host memory free',
        unit='GiB',
    )
    host_memory_percent = Gauge(
        name='host_memory_percent',
        documentation='Host memory percent',
        unit='Percentage',
    )

    gpu_utilization = Gauge(
        name='gpu_utilization',
        documentation='GPU utilization',
        labelnames=['index', 'uuid'],
        unit='Percentage',
    )
    gpu_memory_utilization = Gauge(
        name='gpu_memory_utilization',
        documentation='GPU memory utilization',
        labelnames=['index', 'uuid'],
        unit='Percentage',
    )
    gpu_memory_total = Gauge(
        name='gpu_memory_total',
        documentation='GPU memory total',
        labelnames=['index', 'uuid'],
        unit='GiB',
    )
    gpu_memory_used = Gauge(
        name='gpu_memory_used',
        documentation='GPU memory used',
        labelnames=['index', 'uuid'],
        unit='GiB',
    )
    gpu_memory_free = Gauge(
        name='gpu_memory_free',
        documentation='GPU memory free',
        labelnames=['index', 'uuid'],
        unit='GiB',
    )
    gpu_memory_percent = Gauge(
        name='gpu_memory_percent',
        documentation='GPU memory percent',
        labelnames=['index', 'uuid'],
        unit='Percentage',
    )
    gpu_power_usage = Gauge(
        name='gpu_power_usage',
        documentation='GPU power usage',
        labelnames=['index', 'uuid'],
        unit='W',
    )
    gpu_power_limit = Gauge(
        name='gpu_power_limit',
        documentation='GPU power limit',
        labelnames=['index', 'uuid'],
        unit='W',
    )
    gpu_temperature = Gauge(
        name='gpu_temperature',
        documentation='GPU temperature',
        labelnames=['index', 'uuid'],
        unit='C',
    )
    gpu_fan_speed = Gauge(
        name='gpu_fan_speed',
        documentation='GPU fan speed',
        labelnames=['index', 'uuid'],
        unit='Percentage',
    )

    # Set the host metrics
    host_cpu_percent.set_function(host.cpu_percent)
    host_memory_total.set_function(lambda: (host.virtual_memory().total / GiB))
    host_memory_used.set_function(lambda: (host.virtual_memory().used / GiB))
    host_memory_free.set_function(lambda: (host.virtual_memory().free / GiB))
    host_memory_percent.set_function(lambda: host.virtual_memory().percent)

    # Iterate over all devices
    devices = Device.all()

    def enable(device: Device) -> None:
        index = (
            str(device.index) if isinstance(device.index, int) else ':'.join(map(str, device.index))
        )
        uuid = device.uuid()
        gpu_utilization.labels(index=index, uuid=uuid).set_function(
            device.gpu_utilization,
        )
        gpu_memory_utilization.labels(index=index, uuid=uuid).set_function(
            device.memory_utilization,
        )
        gpu_memory_total.labels(index=index, uuid=uuid).set_function(
            lambda: (device.memory_total() / GiB),
        )
        gpu_memory_used.labels(index=index, uuid=uuid).set_function(
            lambda: (device.memory_used() / GiB),
        )
        gpu_memory_free.labels(index=index, uuid=uuid).set_function(
            lambda: (device.memory_free() / GiB),
        )
        gpu_memory_percent.labels(index=index, uuid=uuid).set_function(
            device.memory_percent,
        )
        gpu_power_usage.labels(index=index, uuid=uuid).set_function(
            lambda: (device.power_usage() / 1000.0),
        )
        gpu_power_limit.labels(index=index, uuid=uuid).set_function(
            lambda: (device.power_limit() / 1000.0),
        )
        gpu_temperature.labels(index=index, uuid=uuid).set_function(
            device.temperature,
        )
        gpu_fan_speed.labels(index=index, uuid=uuid).set_function(
            device.fan_speed,
        )

    physical_devices = Device.all()
    devices = []
    leaf_devices = []
    for physical_device in physical_devices:
        devices.append(physical_device)
        mig_devices = physical_device.mig_devices()
        if len(mig_devices) > 0:
            devices.extend(mig_devices)
            leaf_devices.extend(mig_devices)
        else:
            leaf_devices.append(physical_device)

    for device in devices:
        enable(device)

    start_http_server(args.port, args.host)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == '__main__':
    sys.exit(main())
