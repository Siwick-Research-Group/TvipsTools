from os import path
import logging as log
import numpy as np
from PyQt5 import QtWidgets, QtCore, QtGui, uic
import pyqtgraph as pg
from .. import get_base_path
from ..lib.uiutils import (
    TvipsLiveImageGrabber,
    TvipsAcquisitionImageGrabber,
    interrupt_acquisition,
    RectROI,
    ExposureActionSlider
)
from .widgets import ROIView, ImageViewWidget


class LiveViewUi(QtWidgets.QMainWindow):
    """
    main window of the LiveView application
    """

    image = None
    dark_image = None
    i_digits = 5
    update_interval = None

    def __init__(self, cmd_args, *args, **kwargs):
        log.debug("initializing TvipsLiveView")
        super().__init__(*args, **kwargs)
        uic.loadUi(path.join(get_base_path(), "ui/liveview.ui"), self)
        self.settings = QtCore.QSettings("Siwick Research Group", "TvipsTools Liveview", parent=self)
        if self.settings.value("main_window_geometry") is not None:
            self.setGeometry(self.settings.value("main_window_geometry"))
        if self.settings.value("pin_histogram_zero") is not None:
            pin_zero = self.settings.value("pin_histogram_zero").lower() == "true"
            self.actionPinHistogramZero.setChecked(pin_zero)
        if self.settings.value("auto_levels") is not None:
            auto_levels = self.settings.value("auto_levels").lower() == "true"
            self.viewer.view.menu.autoLevels.setChecked(auto_levels)
            if not auto_levels:
                if self.settings.value("image_levels") is not None:
                    self.viewer.setLevels(*self.settings.value("image_levels"))
                    self.viewer.setHistogramRange(*self.settings.value("image_levels"))
                if self.settings.value("histogram_range") is not None:
                    self.viewer.ui.histogram.setHistogramRange(*self.settings.value("histogram_range"), padding=0)
        if self.settings.value("dark_image") is not None:
            self.dark_image = self.settings.value("dark_image")

        self.update_interval = cmd_args.update_interval

        self.tvips_image_grabber = TvipsLiveImageGrabber(cmd_args.camera)
        self.tvips_image_acquirer = TvipsAcquisitionImageGrabber(cmd_args.camera)

        self.image_timer = QtCore.QTimer()
        self.image_timer.timeout.connect(self.tvips_image_grabber.image_grabber_thread.start)
        self.tvips_image_grabber.image_ready.connect(self.update_image)
        self.tvips_image_acquirer.image_ready.connect(self.display_acquired_image)

        self.labelIntensity = QtWidgets.QLabel()
        self.labelState = QtWidgets.QLabel()
        self.labelTrigger = QtWidgets.QLabel()
        self.labelCmode = QtWidgets.QLabel()
        self.labelStop = QtWidgets.QLabel()

        self.init_menubar()
        self.init_statusbar()

        self.roi_view = ROIView(title="ROIs")
        self.acquired_image_views = []

        self.show()

    def closeEvent(self, evt):
        self.roi_view.hide()
        for i in self.viewer.view.addedItems:
            if isinstance(i, RectROI):
                i.win.hide()
        self.settings.setValue("main_window_geometry", self.geometry())
        self.settings.setValue("image_levels", self.viewer.getLevels())
        self.settings.setValue("auto_levels", self.viewer.view.menu.autoLevels.isChecked())
        hist_range = tuple(self.viewer.ui.histogram.item.vb.viewRange()[1])  # wtf?
        self.settings.setValue("histogram_range", hist_range)
        self.settings.setValue("pin_histogram_zero", self.actionPinHistogramZero.isChecked())
        self.settings.setValue("dark_image", self.dark_image)
        self.hide()
        self.image_timer.stop()
        self.tvips_image_grabber.image_grabber_thread.requestInterruption()
        self.tvips_image_grabber.image_grabber_thread.wait()
        if self.tvips_image_grabber.connected and self.actionStart.isChecked():
            self.tvips_image_grabber.f216.StopLive()
        super().closeEvent(evt)

    def init_statusbar(self):
        self.viewer.cursor_changed.connect(self.update_label_intensity)

        status_label_font = QtGui.QFont("Courier", 9)
        self.labelIntensity.setFont(status_label_font)
        self.labelState.setFont(status_label_font)
        self.labelTrigger.setFont(status_label_font)
        self.labelCmode.setFont(status_label_font)
        self.labelStop.setFont(status_label_font)
        self.labelStop.setMinimumWidth(15)
        self.labelStop.setText("🛑")

        self.labelIntensity.setText(f'({"":>4s}, {"":>4s})   {"":>{self.i_digits}s}')
        fake_spacer = QtWidgets.QLabel()  # status bar does not accect QSpacerItem
        fake_spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)

        self.statusbar.addPermanentWidget(fake_spacer)
        self.statusbar.addPermanentWidget(self.labelIntensity)
        self.statusbar.addPermanentWidget(self.labelState)
        self.statusbar.addPermanentWidget(self.labelTrigger)
        self.statusbar.addPermanentWidget(self.labelCmode)
        self.statusbar.addPermanentWidget(self.labelStop)

    def init_menubar(self):
        self.actionAddRectangle.triggered.connect(self.add_rect_roi)
        self.actionAddRectangle.setShortcut("R")
        self.actionRemoveLastROI.triggered.connect(self.remove_last_roi)
        self.actionRemoveLastROI.setShortcut("Shift+R")
        self.actionRemoveAllROIs.triggered.connect(self.remove_all_rois)
        self.actionRemoveAllROIs.setShortcut("Ctrl+Shift+R")
        self.actionLinkYAxis.triggered.connect(self.update_y_axis_link)
        self.actionLinkYAxis.setShortcut("Y")
        self.actionShowProjections.setShortcut("P")
        self.actionShowCrosshair.setShortcut("C")
        self.actionShowCrosshair.triggered.connect(
            lambda x=self.actionShowCrosshair.isChecked(): self.viewer.show_crosshair(x)
        )
        self.actionShowMaxPixelValue.setShortcut("M")
        self.actionShowFrame.triggered.connect(lambda x=self.actionShowFrame.isChecked(): self.viewer.show_frame(x))
        self.actionShowFrame.setShortcut("F")
        self.actionPinHistogramZero.setShortcut("H")
        self.actionPinHistogramZero.triggered.connect(self.pin_histogram_zero)
        self.viewer.ui.histogram.sigLevelsChanged.connect(self.pin_histogram_zero)
        self.viewer.ui.histogram.item.vb.sigRangeChangedManually.connect(self.pin_histogram_zero)

        running_group = QtWidgets.QActionGroup(self)
        running_group.addAction(self.actionStart)
        running_group.addAction(self.actionStop)
        running_group.triggered.connect(self.update_running)
        self.actionStart.setShortcut("Space")
        self.actionStop.setShortcut("Esc")

        self.actionExposureSlider = ExposureActionSlider()
        self.actionTakeImage.triggered.connect(self.acquire_image)
        self.menuDetector.addAction(self.actionExposureSlider)

        self.menuDetector.addSeparator()
        self.actionTakeDark = QtWidgets.QAction("take dark")
        self.menuDetector.addAction(self.actionTakeDark)
        self.actionTakeDark.triggered.connect(self.set_dark)
        self.actionClearDark = QtWidgets.QAction("clear dark")
        self.actionClearDark.triggered.connect(self.clear_dark)
        self.menuDetector.addAction(self.actionClearDark)

    @QtCore.pyqtSlot(tuple)
    def update_label_intensity(self, xy):
        if self.image is None or xy == (np.NaN, np.NaN):
            self.labelIntensity.setText(f'({"":>4s}, {"":>4s})   {"":>{self.i_digits}s}')
            return
        x, y = xy
        i = self.image[x, y]
        self.labelIntensity.setText(f"({x:>4}, {y:>4}) I={i:>{self.i_digits}.0f}")

    def acquire_image(self):
        self.tvips_image_acquirer.exposure = self.actionExposureSlider.exposure
        self.tvips_image_acquirer.acquire_image()

    @QtCore.pyqtSlot()
    def set_dark(self):
        self.dark_image = self.image

    @QtCore.pyqtSlot()
    def clear_dark(self):
        self.dark_image = None

    @QtCore.pyqtSlot(np.ndarray)
    def display_acquired_image(self, image):
        if self.dark_image is not None:
            image = image.astype(np.int32) - self.dark_image
        ivw = ImageViewWidget()
        self.acquired_image_views.append(ivw)
        ivw.setImage(image)
        ivw.show()

    @QtCore.pyqtSlot(np.ndarray)
    def update_image(self, image):
        if self.dark_image is not None:
            self.image = image.astype(np.int32) - self.dark_image
        else:
            self.image = image
        self.viewer.clear()
        self.viewer.setImage(
            self.image,
            max_label=self.actionShowMaxPixelValue.isChecked(),
            projections=self.actionShowProjections.isChecked(),
        )
        self.i_digits = len(str(int(image.max(initial=1))))
        self.update_all_rois()

    @QtCore.pyqtSlot()
    def update_running(self):
        if self.actionStop.isChecked():
            if self.tvips_image_grabber.connected:
                self.tvips_image_grabber.f216.StopLive()
            self.labelStop.setText("🛑")
            self.actionTakeImage.setEnabled(True)
            self.image_timer.stop()
        else:
            if self.tvips_image_grabber.connected:
                self.tvips_image_grabber.f216.StartLive()
            self.labelStop.setText("💚")
            self.actionTakeImage.setEnabled(False)
            self.image_timer.start(self.update_interval)

    @QtCore.pyqtSlot()
    def add_rect_roi(self):
        if self.image is not None:
            log.info("added rectangular ROI")
            roi = RectROI(
                (self.image.shape[0] / 2 - 50, self.image.shape[1] / 2 - 50),
                (100, 100),
                centered=True,
                sideScalers=True,
                pen=pg.mkPen("c", width=2),
                hoverPen=pg.mkPen("c", width=3),
                handlePen=pg.mkPen("w", width=3),
                handleHoverPen=pg.mkPen("w", width=4),
            )
            roi.removable = True
            roi.sigRemoveRequested.connect(self.remove_roi)
            roi.sigRegionChangeFinished.connect(self.update_roi)

            self.viewer.addItem(roi)
            roi.plot_item = self.roi_view.addPlot()
            roi.plot_item.setMouseEnabled(x=False, y=True)
            if len(self.roi_view) > 1 and self.actionLinkYAxis.isChecked():
                roi.plot_item.setYLink(self.roi_view.plots[0])

            self.roi_view.rearrange()
            self.update_roi(roi)
            self.roi_view.show()

        else:
            log.warning("cannot add ROI before an image is dislayed")

    @QtCore.pyqtSlot(tuple)
    def update_roi(self, roi):
        roi_data = roi.getArrayRegion(self.image, self.viewer.imageItem)
        roi.add_mean(self.image, self.viewer.imageItem)
        roi.plot_item.clear()
        roi.plot_item.plot(roi_data.mean(axis=np.argmin(roi_data.shape)))

    def update_all_rois(self):
        for i in self.viewer.view.addedItems:
            if isinstance(i, RectROI):
                try:
                    self.update_roi(i)
                except Exception:  # bad practice, but works for now...
                    pass

    @QtCore.pyqtSlot(tuple)
    def remove_roi(self, roi):
        self.viewer.scene.removeItem(roi)
        self.roi_view.removeItem(roi.plot_item)
        roi.win.hide()
        if len(self.roi_view) == 0:
            self.roi_view.hide()

    @QtCore.pyqtSlot()
    def update_y_axis_link(self):
        self.roi_view.set_link_y_axis(self.actionLinkYAxis.isChecked())

    @QtCore.pyqtSlot()
    def remove_last_roi(self):
        for i in self.viewer.view.addedItems[::-1]:
            if isinstance(i, pg.ROI):
                try:
                    self.remove_roi(i)
                    return
                except Exception:  # again bad practice, but works...
                    pass

    @QtCore.pyqtSlot()
    def remove_all_rois(self):
        for i in self.viewer.view.addedItems[::-1]:
            if isinstance(i, pg.ROI):
                try:
                    self.remove_roi(i)
                except Exception:  # again bad practice, but works...
                    pass

    @QtCore.pyqtSlot()
    def pin_histogram_zero(self):
        if self.actionPinHistogramZero.isChecked():
            y_view_max = self.viewer.ui.histogram.item.vb.viewRange()[1][1]
            y_limit = -0.01 * y_view_max
            self.viewer.ui.histogram.item.vb.setYRange(y_limit, y_view_max, padding=0)

            y_level_min, y_level_max = self.viewer.ui.histogram.getLevels()
            if y_level_min != 0:
                self.viewer.ui.histogram.setLevels(0, y_level_max)
