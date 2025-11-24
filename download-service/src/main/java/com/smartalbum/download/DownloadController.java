package com.smartalbum.download;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.HashMap;
import java.util.Map;

@RestController
@RequestMapping("/api/albums")
@CrossOrigin(origins = "*")
public class DownloadController {
    private static final Logger logger = LoggerFactory.getLogger(DownloadController.class);

    @Autowired
    private DownloadService downloadService;

    @GetMapping("/{albumId}/download")
    public ResponseEntity<Map<String, String>> getDownloadUrl(@PathVariable("albumId") String albumId) {
        logger.info("Download request received: albumId={}", albumId);

        try {
            String downloadUrl = downloadService.getDownloadUrl(albumId);

            Map<String, String> response = new HashMap<>();
            response.put("albumId", albumId);
            response.put("downloadUrl", downloadUrl);
            response.put("expiresIn", "3600 seconds");

            logger.info("Download URL generated successfully: albumId={}", albumId);
            return ResponseEntity.ok(response);

        } catch (IllegalArgumentException e) {
            logger.error("Validation error: {}", e.getMessage());
            Map<String, String> error = new HashMap<>();
            error.put("error", e.getMessage());
            return ResponseEntity.badRequest().body(error);

        } catch (Exception e) {
            logger.error("Failed to generate download URL for albumId={}", albumId, e);
            Map<String, String> error = new HashMap<>();
            error.put("error", "Failed to generate download URL: " + e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(error);
        }
    }
}
