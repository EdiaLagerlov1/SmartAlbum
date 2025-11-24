package com.smartalbum.upload;

import com.smartalbum.common.Album;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/albums")
@CrossOrigin(origins = "*")
public class UploadController {
    private static final Logger logger = LoggerFactory.getLogger(UploadController.class);

    @Autowired
    private UploadService uploadService;

    @PostMapping("/upload")
    public ResponseEntity<Map<String, String>> uploadAlbum(
            @RequestParam("albumName") String albumName,
            @RequestParam("images") MultipartFile[] images) {

        logger.info("Received upload request: albumName={}, imageCount={}", albumName, images.length);

        try {
            String albumId = uploadService.processUpload(albumName, images);

            Map<String, String> response = new HashMap<>();
            response.put("albumId", albumId);
            response.put("status", "PROCESSING");
            response.put("message", "Album upload successful. PDF generation in progress.");

            logger.info("Upload completed successfully: albumId={}", albumId);
            return ResponseEntity.ok(response);

        } catch (IllegalArgumentException e) {
            logger.error("Validation error: {}", e.getMessage());
            Map<String, String> error = new HashMap<>();
            error.put("error", e.getMessage());
            return ResponseEntity.badRequest().body(error);

        } catch (Exception e) {
            logger.error("Upload failed", e);
            Map<String, String> error = new HashMap<>();
            error.put("error", "Failed to process upload: " + e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }

    @GetMapping("/status/{albumId}")
    public ResponseEntity<Map<String, String>> getAlbumStatus(@PathVariable("albumId") String albumId) {
        logger.info("Status check request: albumId={}", albumId);

        try {
            Album album = uploadService.getAlbumStatus(albumId);

            if (album == null) {
                Map<String, String> error = new HashMap<>();
                error.put("error", "Album not found");
                return ResponseEntity.status(HttpStatus.NOT_FOUND).body(error);
            }

            Map<String, String> response = new HashMap<>();
            response.put("albumId", album.getAlbumId());
            response.put("albumName", album.getAlbumName());
            response.put("status", album.getStatus().name());

            return ResponseEntity.ok(response);

        } catch (Exception e) {
            logger.error("Failed to get status", e);
            Map<String, String> error = new HashMap<>();
            error.put("error", "Failed to get album status");
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }
}
