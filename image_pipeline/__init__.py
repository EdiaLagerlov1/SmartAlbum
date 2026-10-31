"""SmartAlbum - image_pipeline package.

ראה image_pipeline/api.py ו-INTERFACES.md סעיף 4.1 לחוזה המלא.
"""

from image_pipeline.api import PhotoUploadResult, group_similar_photos, process_upload

__all__ = ["PhotoUploadResult", "process_upload", "group_similar_photos"]
