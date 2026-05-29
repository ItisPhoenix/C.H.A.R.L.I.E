import glob
import os

from charlie.utils.logger import get_logger

logger = get_logger("CUDAHelper")


def setup_cuda_paths():
    """
    Scans the system for cuDNN/CUDA DLLs and adds them to the DLL search path.
    This resolves the 'missing cudnn_graph64_9.dll' issues on Windows without
    forcing CPU fallback.
    """

    if os.name != "nt":
        return

    # Potential root directories for cuDNN/CUDA on Windows
    search_roots = [
        os.environ.get("CUDNN_PATH"),
        os.environ.get("CUDA_PATH"),
        "C:\\Program Files\\NVIDIA\\CUDNN",
        "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\12.4",
    ]
    # Filter None values
    search_roots = [r for r in search_roots if r and os.path.exists(r)]

    added_count = 0

    # We are specifically looking for directories containing cudnn*.dll
    for root in search_roots:
        if not os.path.exists(root):
            continue

        # Recursive search for bin directories containing DLLs
        # Pattern covers v9.x/bin/12.x/x64 etc.
        pattern = os.path.join(root, "**", "bin", "**", "*.dll")
        dll_files = glob.glob(pattern, recursive=True)

        # Extract unique directories containing DLLs
        dll_dirs = set(os.path.dirname(f) for f in dll_files)

        for d in dll_dirs:
            try:
                # Use os.add_dll_directory for Python 3.8+
                os.add_dll_directory(d)
                # Also add to PATH for legacy compatibility
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                logger.info(f"added_dll_directory | path={d}")
                added_count += 1
            except Exception as e:
                logger.debug(f"dll_dir_add_failed | path={d} | error={e}")

    if added_count == 0:
        logger.warning("no_cuda_dll_dirs_found | system_inference_may_fail")
    else:
        logger.info(f"cuda_paths_stabilized | dirs_added={added_count}")
