from __future__ import annotations

from enum import Enum, auto

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsSceneHoverEvent,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

HANDLE_SIZE = 8.0


class Handle(Enum):
    TL = auto()
    TC = auto()
    TR = auto()
    ML = auto()
    MR = auto()
    BL = auto()
    BC = auto()
    BR = auto()


class OverlaySignals(QObject):
    position_changed = Signal(float, float, float, float)  # x, y, w, h fractions


class OverlayItem(QGraphicsPixmapItem):
    def __init__(
        self,
        pixmap: QPixmap,
        scene_w: float,
        scene_h: float,
    ) -> None:
        super().__init__(pixmap)
        self.signals = OverlaySignals()
        self._scene_w = scene_w
        self._scene_h = scene_h
        self._original_pixmap = pixmap
        self._active_handle: Handle | None = None
        self._drag_start: QPointF = QPointF()
        self._rect_start: QRectF = QRectF()

        self.setFlags(
            self.GraphicsItemFlag.ItemIsMovable
            | self.GraphicsItemFlag.ItemIsSelectable
            | self.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

    def set_scene_size(self, w: float, h: float) -> None:
        self._scene_w = w
        self._scene_h = h

    def get_fractional_rect(self) -> tuple[float, float, float, float]:
        rect = self.sceneBoundingRect()
        if self._scene_w == 0 or self._scene_h == 0:
            return (0, 0, 0, 0)
        return (
            rect.x() / self._scene_w,
            rect.y() / self._scene_h,
            rect.width() / self._scene_w,
            rect.height() / self._scene_h,
        )

    def set_from_fractions(self, x: float, y: float, w: float, h: float) -> None:
        px_w = max(1, int(w * self._scene_w))
        px_h = max(1, int(h * self._scene_h))
        scaled = self._original_pixmap.scaled(
            px_w, px_h, Qt.AspectRatioMode.IgnoreAspectRatio
        )
        self.setPixmap(scaled)
        self.setPos(x * self._scene_w, y * self._scene_h)

    def _handle_rects(self) -> dict[Handle, QRectF]:
        r = self.boundingRect()
        hs = HANDLE_SIZE
        cx = r.center().x()
        cy = r.center().y()
        return {
            Handle.TL: QRectF(r.left() - hs / 2, r.top() - hs / 2, hs, hs),
            Handle.TC: QRectF(cx - hs / 2, r.top() - hs / 2, hs, hs),
            Handle.TR: QRectF(r.right() - hs / 2, r.top() - hs / 2, hs, hs),
            Handle.ML: QRectF(r.left() - hs / 2, cy - hs / 2, hs, hs),
            Handle.MR: QRectF(r.right() - hs / 2, cy - hs / 2, hs, hs),
            Handle.BL: QRectF(r.left() - hs / 2, r.bottom() - hs / 2, hs, hs),
            Handle.BC: QRectF(cx - hs / 2, r.bottom() - hs / 2, hs, hs),
            Handle.BR: QRectF(r.right() - hs / 2, r.bottom() - hs / 2, hs, hs),
        }

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setPen(QPen(Qt.GlobalColor.blue, 1.5))
            for rect in self._handle_rects().values():
                painter.fillRect(rect, Qt.GlobalColor.white)
                painter.drawRect(rect)

    _HANDLE_CURSORS: dict[Handle, Qt.CursorShape] = {
        Handle.TL: Qt.CursorShape.SizeFDiagCursor,
        Handle.BR: Qt.CursorShape.SizeFDiagCursor,
        Handle.TR: Qt.CursorShape.SizeBDiagCursor,
        Handle.BL: Qt.CursorShape.SizeBDiagCursor,
        Handle.TC: Qt.CursorShape.SizeVerCursor,
        Handle.BC: Qt.CursorShape.SizeVerCursor,
        Handle.ML: Qt.CursorShape.SizeHorCursor,
        Handle.MR: Qt.CursorShape.SizeHorCursor,
    }

    def hoverMoveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # type: ignore[override]
        for handle, rect in self._handle_rects().items():
            if rect.contains(event.pos()):
                self.setCursor(QCursor(self._HANDLE_CURSORS[handle]))
                return
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    def hoverLeaveEvent(self, event: QGraphicsSceneHoverEvent) -> None:  # type: ignore[override]
        self.unsetCursor()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        pos = event.pos()
        for handle, rect in self._handle_rects().items():
            if rect.contains(pos):
                self._active_handle = handle
                self._drag_start = event.scenePos()
                self._rect_start = self.sceneBoundingRect()
                event.accept()
                return
        self._active_handle = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        if self._active_handle is None:
            super().mouseMoveEvent(event)
            return

        delta = event.scenePos() - self._drag_start
        r = QRectF(self._rect_start)
        h = self._active_handle
        min_sz = 10.0

        if h in (Handle.TL, Handle.ML, Handle.BL):
            r.setLeft(min(r.left() + delta.x(), r.right() - min_sz))
        if h in (Handle.TR, Handle.MR, Handle.BR):
            r.setRight(max(r.right() + delta.x(), r.left() + min_sz))
        if h in (Handle.TL, Handle.TC, Handle.TR):
            r.setTop(min(r.top() + delta.y(), r.bottom() - min_sz))
        if h in (Handle.BL, Handle.BC, Handle.BR):
            r.setBottom(max(r.bottom() + delta.y(), r.top() + min_sz))

        # Clamp to scene
        if r.left() < 0:
            r.moveLeft(0)
        if r.top() < 0:
            r.moveTop(0)
        if r.right() > self._scene_w:
            r.moveRight(self._scene_w)
        if r.bottom() > self._scene_h:
            r.moveBottom(self._scene_h)

        # Apply
        pw = max(1, int(r.width()))
        ph = max(1, int(r.height()))
        scaled = self._original_pixmap.scaled(
            pw, ph, Qt.AspectRatioMode.IgnoreAspectRatio
        )
        self.setPixmap(scaled)
        self.setPos(r.topLeft())
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        if self._active_handle is not None:
            self._active_handle = None
            x, y, w, h = self.get_fractional_rect()
            self.signals.position_changed.emit(x, y, w, h)
            event.accept()
            return
        super().mouseReleaseEvent(event)
        x, y, w, h = self.get_fractional_rect()
        self.signals.position_changed.emit(x, y, w, h)
