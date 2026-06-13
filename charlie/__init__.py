import logging
import os
import sys

logger = logging.getLogger(__name__)

# Windows/Python 3.12+ DLL Search Hardening MUST happen at the very start of the process
if os.name == "nt":
    # 1. Search roots (Current env + Project venv)
    roots = [
        sys.prefix,
        os.getcwd(),
        os.path.join(os.getcwd(), ".venv"),
        os.environ.get("CUDA_PATH", "C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA\\v13.1"),
        os.path.join(os.environ.get("CUDNN_PATH", "C:\\Program Files\\NVIDIA\\CUDNN\\v9.21"), "bin", "12.9", "x64"),
        os.path.join(os.environ.get("CUDNN_PATH", "C:\\Program Files\\NVIDIA\\CUDNN\\v9.21"), "bin", "13.2", "x64"),
    ]
    seen_paths = set()

    for root in roots:
        sp_path = os.path.join(root, "Lib", "site-packages")
        if not os.path.exists(sp_path):
            continue

        # A. Register nvidia subfolders (CUDA 12.x / cuDNN 9.x)
        nvidia_root = os.path.join(sp_path, "nvidia")
        if os.path.exists(nvidia_root):
            cuda_libs = [
                "cublas",
                "cuda_nvrtc",
                "cudnn",
                "cuda_runtime",
                "cufft",
                "curand",
                "cusolver",
                "cusparse",
                "nvjitlink",
            ]
            for sub in cuda_libs:
                bin_dir = os.path.join(nvidia_root, sub, "bin")
                if os.path.exists(bin_dir) and bin_dir not in seen_paths:
                    try:
                        os.add_dll_directory(bin_dir)
                        # Prepend to PATH for libraries using legacy LoadLibrary
                        os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
                        seen_paths.add(bin_dir)
                    except Exception as e:
                        logger.debug(f"dll_add_failed | lib={sub} | path={bin_dir} | error={e}")

        # B. Register primary ML folders (ONNX, Ctranslate2)
        primary_dirs = [
            os.path.join(sp_path, "onnxruntime", "capi"),
            os.path.join(sp_path, "ctranslate2"),
        ]
        for pdir in primary_dirs:
            if os.path.exists(pdir) and pdir not in seen_paths:
                try:
                    os.add_dll_directory(pdir)
                    os.environ["PATH"] = pdir + os.pathsep + os.environ["PATH"]
                    seen_paths.add(pdir)
                except Exception as e:
                    logger.debug(f"primary_dll_add_failed | path={pdir} | error={e}")

    # C. Register system bin folders
    for root in roots:
        bin_dir = os.path.join(root, "bin")
        if os.path.exists(bin_dir) and bin_dir not in seen_paths:
            try:
                os.add_dll_directory(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ["PATH"]
                seen_paths.add(bin_dir)
            except Exception as e:
                logger.debug(f"bin_dll_add_failed | path={bin_dir} | error={e}")
        elif os.path.exists(root) and "site-packages" not in root and root not in seen_paths:
            # Direct registration for CUDNN paths that end in x64
            try:
                os.add_dll_directory(root)
                os.environ["PATH"] = root + os.pathsep + os.environ["PATH"]
                seen_paths.add(root)
            except Exception as e:
                logger.debug(f"root_dll_add_failed | path={root} | error={e}")
