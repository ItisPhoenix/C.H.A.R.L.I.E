from PyInstaller.utils.hooks import exclude_module

# Exclude missing optional submodules that cause build crashes
exclude_module("tensorboard")
exclude_module("dask")
exclude_module("webrtcvad")
