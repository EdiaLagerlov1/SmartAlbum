package com.smartalbum.download;

import com.smartalbum.common.Album;
import com.smartalbum.common.DynamoDBService;
import com.smartalbum.common.S3Service;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.Duration;

@Service
public class DownloadService {
    private static final Logger logger = LoggerFactory.getLogger(DownloadService.class);
    private static final Duration URL_EXPIRATION = Duration.ofHours(1);

    @Autowired
    private S3Service s3Service;

    @Autowired
    private DynamoDBService dynamoDBService;

    public String getDownloadUrl(String albumId) {
        logger.info("Generating download URL: albumId={}", albumId);

        Album album = dynamoDBService.getAlbum(albumId);

        if (album == null) {
            logger.warn("Album not found: albumId={}", albumId);
            throw new IllegalArgumentException("Album not found: " + albumId);
        }

        if (album.getStatus() != Album.AlbumStatus.READY) {
            logger.warn("Album not ready: albumId={}, status={}", albumId, album.getStatus());
            throw new IllegalArgumentException("Album is not ready. Current status: " +
                    album.getStatus());
        }

        if (album.getPdfKey() == null || album.getPdfKey().isEmpty()) {
            logger.error("PDF key is null for album: albumId={}", albumId);
            throw new IllegalArgumentException("PDF not found for album");
        }

        String downloadUrl = s3Service.generatePresignedUrl(album.getPdfKey(), URL_EXPIRATION);

        logger.info("Download URL generated successfully: albumId={}", albumId);
        return downloadUrl;
    }
}
