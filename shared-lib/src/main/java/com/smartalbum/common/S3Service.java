package com.smartalbum.common;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.*;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;
import software.amazon.awssdk.services.s3.presigner.model.PresignedGetObjectRequest;

import java.io.InputStream;
import java.time.Duration;

public class S3Service {
    private static final Logger logger = LoggerFactory.getLogger(S3Service.class);
    private final S3Client s3Client;
    private final S3Presigner s3Presigner;
    private final String bucketName;

    public S3Service(S3Client s3Client, S3Presigner s3Presigner, String bucketName) {
        this.s3Client = s3Client;
        this.s3Presigner = s3Presigner;
        this.bucketName = bucketName;
    }

    public void uploadFile(String key, InputStream inputStream, long contentLength, String contentType) {
        logger.info("Uploading file to S3: bucket={}, key={}", bucketName, key);
        try {
            PutObjectRequest request = PutObjectRequest.builder()
                    .bucket(bucketName)
                    .key(key)
                    .contentType(contentType)
                    .contentLength(contentLength)
                    .build();

            s3Client.putObject(request, RequestBody.fromInputStream(inputStream, contentLength));
            logger.info("Successfully uploaded file: {}", key);
        } catch (Exception e) {
            logger.error("Failed to upload file: {}", key, e);
            throw new RuntimeException("Failed to upload file to S3", e);
        }
    }

    public InputStream downloadFile(String key) {
        logger.info("Downloading file from S3: bucket={}, key={}", bucketName, key);
        try {
            GetObjectRequest request = GetObjectRequest.builder()
                    .bucket(bucketName)
                    .key(key)
                    .build();

            return s3Client.getObject(request);
        } catch (Exception e) {
            logger.error("Failed to download file: {}", key, e);
            throw new RuntimeException("Failed to download file from S3", e);
        }
    }

    public String generatePresignedUrl(String key, Duration expiration) {
        logger.info("Generating presigned URL: bucket={}, key={}, expiration={}",
                    bucketName, key, expiration);
        try {
            GetObjectRequest getObjectRequest = GetObjectRequest.builder()
                    .bucket(bucketName)
                    .key(key)
                    .build();

            GetObjectPresignRequest presignRequest = GetObjectPresignRequest.builder()
                    .signatureDuration(expiration)
                    .getObjectRequest(getObjectRequest)
                    .build();

            PresignedGetObjectRequest presignedRequest = s3Presigner.presignGetObject(presignRequest);
            String url = presignedRequest.url().toString();
            logger.info("Generated presigned URL successfully");
            return url;
        } catch (Exception e) {
            logger.error("Failed to generate presigned URL: {}", key, e);
            throw new RuntimeException("Failed to generate presigned URL", e);
        }
    }

    public void uploadBytes(String key, byte[] data, String contentType) {
        logger.info("Uploading bytes to S3: bucket={}, key={}, size={}", bucketName, key, data.length);
        try {
            PutObjectRequest request = PutObjectRequest.builder()
                    .bucket(bucketName)
                    .key(key)
                    .contentType(contentType)
                    .build();

            s3Client.putObject(request, RequestBody.fromBytes(data));
            logger.info("Successfully uploaded bytes: {}", key);
        } catch (Exception e) {
            logger.error("Failed to upload bytes: {}", key, e);
            throw new RuntimeException("Failed to upload bytes to S3", e);
        }
    }
}
