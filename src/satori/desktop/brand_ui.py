"""Bundled original portrait; presentation only, never a personality-state owner."""

from importlib.resources import files

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


def portrait(size: int) -> QLabel:
    label = QLabel()
    label.setAccessibleName("Портрет Сатори")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    image = QPixmap()
    image.loadFromData(
        files("satori").joinpath("resources/brand/satori-portrait-v1.png").read_bytes()
    )
    label.setPixmap(
        image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )
    label.setFixedSize(size, size)
    return label
