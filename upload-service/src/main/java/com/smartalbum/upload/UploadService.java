package com.smartalbum.upload;

import com.smartalbum.common.*;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

@Service
public class UploadService {
    private static final Logger logger = LoggerFactory.getLogger(UploadService.class);

    @Autowired
    private S3Service s3Service;

    @Autowired
    private DynamoDBService dynamoDBService;

    @Autowired
    private PdfServiceClient pdfServiceClient;

    public String processUpload(String albumName, MultipartFile[] images) {
        logger.info("Processing upload: albumName={}, imageCount={}", albumName, images.length);

        validateUpload(albumName, images);

        String albumId = UUID.randomUUID().toString();
        Album album = new Album(albumId, albumName);

        List<String> imageKeys = new ArrayList<>();
        for (int i = 0; i < images.length; i++) {
            MultipartFile image = images[i];
            String imageKey = String.format("albums/%s/images/%d_%s",
                    albumId, i, image.getOriginalFilename());

            logger.info("Uploading image {}: key={}, size={}", i, imageKey, image.getSize());

            try {
                s3Service.uploadFile(imageKey, image.getInputStream(),
                        image.getSize(), image.getContentType());
                imageKeys.add(imageKey);
            } catch (Exception e) {
                logger.error("Failed to upload image: {}", imageKey, e);
                throw new RuntimeException("Failed to upload image: " + image.getOriginalFilename(), e);
            }
        }

        album.setImageKeys(imageKeys);
        album.setStatus(Album.AlbumStatus.PROCESSING);
        dynamoDBService.saveAlbum(album);

        logger.info("Triggering PDF generation for albumId={}", albumId);
        pdfServiceClient.triggerPdfGeneration(albumId);

        return albumId;
    }

    private void validateUpload(String albumName, MultipartFile[] images) {
        if (albumName == null || albumName.trim().isEmpty()) {
            throw new IllegalArgumentException("Album name is required");
        }

        if (images == null || images.length == 0) {
            throw new IllegalArgumentException("At least one image is required");
        }

        if (images.length > Config.MAX_IMAGES) {
            throw new IllegalArgumentException("Maximum " + Config.MAX_IMAGES + " images allowed");
        }

        for (MultipartFile image : images) {
            if (image.isEmpty()) {
                throw new IllegalArgumentException("Empty image file detected");
            }

            if (image.getSize() > Config.MAX_IMAGE_SIZE) {
                throw new IllegalArgumentException("Image size exceeds maximum: " +
                        image.getOriginalFilename());
            }

            String contentType = image.getContentType();
            if (contentType == null ||
                    Arrays.stream(Config.ALLOWED_CONTENT_TYPES).noneMatch(contentType::equals)) {
                throw new IllegalArgumentException("Invalid image format: " +
                        image.getOriginalFilename());
            }
        }
    }

    public Album getAlbumStatus(String albumId) {
        logger.info("Getting album status: albumId={}", albumId);
        return dynamoDBService.getAlbum(albumId);
    }
}
