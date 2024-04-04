"""
collection of helper classes and functions
"""
from time import sleep
import logging as log
from collections import deque
from PyQt5.QtCore import pyqtSignal, pyqtSlot, QObject, QThread, Qt
from PyQt5.QtWidgets import QWidgetAction, QMenu, QWidget, QHBoxLayout, QSlider, QLabel, QAction
import numpy as np
import pyqtgraph as pg
import tango
from tango import DevState


def fix_image_orientation(image):
    return np.fliplr(np.rot90(image))


class TvipsLiveImageGrabber(QObject):
    """
    class capable of grabbing live images in a non-blocking fashion
    """

    image_ready = pyqtSignal(np.ndarray)
    exposure_triggered = pyqtSignal()
    connected = False

    def __init__(self, camera):
        super().__init__()

        self.f216 = tango.DeviceProxy(camera)
        try:
            if self.f216.state() in (DevState.ON, DevState.RUNNING, DevState.OPEN):
                self.connected = True
            log.info(f"TvipsLiveImageGrabber successfully connected to camera\n{camera}")
        except Exception:
            log.warning("TvipsLiveImageGrabber could not establish connection to camera")

        self.image_grabber_thread = QThread()
        self.moveToThread(self.image_grabber_thread)
        self.image_grabber_thread.started.connect(self.__get_image)

    @pyqtSlot()
    def __get_image(self):
        """
        image collection method
        """
        log.debug(f"started image_grabber_thread {self.image_grabber_thread.currentThread()}")
        if self.connected:
            if self.image_grabber_thread.isInterruptionRequested():
                self.image_grabber_thread.quit()

            try:
                self.image_ready.emit(fix_image_orientation(self.f216.LiveImage))
            except Exception as e:
                log.warning(e)
        else:
            # simulated image for @home use
            self.exposure_triggered.emit()
            sleep(1)
            x = np.linspace(-10, 10, 2048)
            xs, ys = np.meshgrid(x, x)
            img = 5e4 * (
                (np.cos(np.hypot(xs, ys)) / (np.hypot(xs, ys) + 1) * np.random.normal(1, 0.4, (2048, 2048))) + 0.3
            )
            self.image_ready.emit(img)

        self.image_grabber_thread.quit()
        log.debug(f"quit image_grabber_thread {self.image_grabber_thread.currentThread()}")


class TvipsAcquisitionImageGrabber(QObject):
    """
    class capable of acquiring images in a non-blocking fashion
    """

    image_ready = pyqtSignal(np.ndarray)
    connected = False

    def __init__(self, camera):
        super().__init__()

        self.f216 = tango.DeviceProxy(camera)
        try:
            if self.f216.state() in (DevState.ON, DevState.RUNNING, DevState.OPEN):
                self.connected = True
            log.info(f"TvipsLiveImageGrabber successfully connected to camera\n{camera}")
        except Exception:
            log.warning("TvipsLiveImageGrabber could not establish connection to camera")

        self.image_grabber_thread = QThread()
        self.moveToThread(self.image_grabber_thread)
        self.image_grabber_thread.started.connect(self.acquire_image)

    @property
    def exposure(self):
        return self.f216.exposureTime

    @exposure.setter
    def exposure(self, time):
        if self.connected and self.f216.state() == DevState.ON:
            self.f216.exposureTime = time
        else:
            log.warning(f"cannot set exposure time, device state is {self.f216.state()}")

    def acquire_image(self):
        """
        image collection method
        """
        log.debug(f"started image_grabber_thread {self.image_grabber_thread.currentThread()}")
        if self.connected:
            try:
                if self.f216.state() == DevState.ON:
                    self.f216.AcquireAndDisplayImage()
                    while True:
                        if self.image_grabber_thread.isInterruptionRequested():
                            self.image_grabber_thread.quit()
                        sleep(0.25)
                        if self.f216.state() == DevState.ON:
                            break
                    image = self.f216.currentImage
                    self.image_ready.emit(fix_image_orientation(image))
                else:
                    log.warning(f"cannot acquire image, since f216 is in state {self.f216.state()}")
            except Exception as e:
                log.warning(e)
        else:
            # simulated image for @home use
            sleep(1)
            x = np.linspace(-10, 10, 2048)
            xs, ys = np.meshgrid(x, x)
            img = 5e4 * (
                (np.cos(np.hypot(xs, ys)) / (np.hypot(xs, ys) + 1) * np.random.normal(1, 0.4, (2048, 2048))) + 0.3
            )
            self.image_ready.emit(img)

        self.image_grabber_thread.quit()
        log.debug(f"quit image_grabber_thread {self.image_grabber_thread.currentThread()}")


def interrupt_acquisition(f):
    """
    decorator interrupting/resuming image acquisition before/after function call
    """

    def wrapper(self):
        log.debug("stopping liveview")
        self.image_timer.stop()
        if self.tvips_image_grabber.connected:
            if not self.tvips_image_grabber.image_grabber_thread.isFinished():
                log.debug("aborting acquisition")
                self.tvips_image_grabber.image_grabber_thread.requestInterruption()
                self.tvips_image_grabber.image_grabber_thread.wait()
        f(self)
        if not self.actionStop.isChecked():
            log.debug("restarting liveview")
            self.image_timer.start(self.update_interval)

    return wrapper


class RectROI(pg.RectROI):
    mean_thread = QThread()
    update_mean_plot = pyqtSignal()
    __last_img = None
    __last_img_data = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.win = pg.GraphicsLayoutWidget(title="ROI integrated intensity")
        self.plot = self.win.addPlot()
        self.plot.axes["left"]["item"].setLabel("mean intensity")
        self.plot.axes["bottom"]["item"].setLabel("image index")
        self.curve = self.plot.plot()
        self.last_means = deque(maxlen=30)

        self.update_mean_plot.connect(self.__update_mean_plot)
        self.mean_thread.started.connect(self.__compute_mean)

    def getMenu(self):
        if self.menu is None:
            self.menu = QMenu()
            self.menu.setTitle("ROI")
            rem_act = QAction("Remove ROI", self.menu)
            rem_act.triggered.connect(self.removeClicked)
            self.menu.addAction(rem_act)
            self.menu.rem_act = rem_act
            history_act = QAction("Show mean history", self.menu)
            history_act.triggered.connect(self.integral_plot_clicked)
            self.menu.addAction(history_act)
            self.menu.history_act = history_act
        self.menu.setEnabled(self.contextMenuEnabled())
        return self.menu

    def integral_plot_clicked(self):
        print("integral plot clicked")
        self.win.show()
        self.plot.setYRange(0, max(self.last_means) * 2)

    def add_mean(self, data, img):
        self.__last_img_data = data
        self.__last_img = img
        self.mean_thread.start()

    @pyqtSlot()
    def __update_mean_plot(self):
        self.curve.setData(x=range(-len(self.last_means) + 1, 1), y=self.last_means)

    def __compute_mean(self):
        self.last_means.append(self.getArrayRegion(self.__last_img_data, self.__last_img).mean())
        self.mean_thread.quit()
        self.update_mean_plot.emit()


class ActionSlider(QWidgetAction):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.slider = QSlider()
        self.slider.setOrientation(Qt.Horizontal)
        self.slider.valueChanged.connect(self.sliderValueChanged)

        self.label = QLabel('')
        self.label.setAlignment(Qt.AlignCenter)

        layout = QHBoxLayout()
        layout.addWidget(self.slider)
        layout.addWidget(self.label)

        widget = QWidget()
        widget.setLayout(layout)

        self.setDefaultWidget(widget)

    def sliderValueChanged(self, value):
        self.label.setText(str(value))


class ExposureActionSlider(ActionSlider):
    exposures = [200, 500, 1000, 2000, 5000, 10000, 20000, 30000, 40000]

    def __init__(self):
        super().__init__()
        self.slider.setMinimum(0)
        self.slider.setMaximum(len(self.exposures) - 1)
        self.slider.setValue(4)
        self.sliderValueChanged(4)
        self.label.setMinimumWidth(40)

    def sliderValueChanged(self, value):
        self.label.setText(f"{self.exposures[value] / 1000:.1f}s")

    @property
    def exposure(self):
        return self.exposures[self.slider.value()]
