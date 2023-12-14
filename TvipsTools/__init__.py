import os


VERSION = "0.5"
CAMERA_DEVICE = "ElectronMicroscope/Detectors/F216"

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_base_path():
    """
    returns package base dir
    """
    return os.path.dirname(__file__)
