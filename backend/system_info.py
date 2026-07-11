"""Auto-detect system specs and recommend optimal thread count."""
import psutil
import os


def get_system_info():
    cpu_cores = os.cpu_count() or 4
    ram = psutil.virtual_memory()
    ram_gb = round(ram.total / (1024 ** 3), 1)
    ram_available_gb = round(ram.available / (1024 ** 3), 1)

    # Each UC Chrome uses ~200-300MB. Leave 2GB headroom for OS + backend.
    max_by_ram = max(1, int((ram_available_gb - 2) / 0.3))
    max_by_cpu = max(1, cpu_cores - 1)
    # Allow high concurrency for maximum throughput
    recommended = min(max_by_ram, max_by_cpu, 15)

    return {
        "cpu_cores": cpu_cores,
        "ram_total_gb": ram_gb,
        "ram_available_gb": ram_available_gb,
        "recommended_threads": max(1, recommended),
        "max_threads": min(max_by_ram, 60),
    }
