package com.smartalbum.common;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;
import software.amazon.awssdk.services.dynamodb.model.*;

import java.time.Instant;
import java.util.*;
import java.util.stream.Collectors;

public class DynamoDBService {
    private static final Logger logger = LoggerFactory.getLogger(DynamoDBService.class);
    private final DynamoDbClient dynamoDbClient;
    private final String tableName;

    public DynamoDBService(DynamoDbClient dynamoDbClient, String tableName) {
        this.dynamoDbClient = dynamoDbClient;
        this.tableName = tableName;
    }

    public void saveAlbum(Album album) {
        logger.info("Saving album to DynamoDB: albumId={}, albumName={}",
                    album.getAlbumId(), album.getAlbumName());
        try {
            Map<String, AttributeValue> item = new HashMap<>();
            item.put("albumId", AttributeValue.builder().s(album.getAlbumId()).build());
            item.put("albumName", AttributeValue.builder().s(album.getAlbumName()).build());
            item.put("status", AttributeValue.builder().s(album.getStatus().name()).build());
            item.put("createdAt", AttributeValue.builder()
                    .s(album.getCreatedAt().toString()).build());
            item.put("expiresAt", AttributeValue.builder().n(album.getExpiresAt().toString()).build());

            if (album.getImageKeys() != null) {
                List<AttributeValue> imageList = album.getImageKeys().stream()
                        .map(k -> AttributeValue.builder().s(k).build())
                        .collect(Collectors.toList());
                item.put("imageKeys", AttributeValue.builder().l(imageList).build());
            }

            if (album.getPdfKey() != null) {
                item.put("pdfKey", AttributeValue.builder().s(album.getPdfKey()).build());
            }

            PutItemRequest request = PutItemRequest.builder()
                    .tableName(tableName)
                    .item(item)
                    .build();

            dynamoDbClient.putItem(request);
            logger.info("Successfully saved album: {}", album.getAlbumId());
        } catch (Exception e) {
            logger.error("Failed to save album: {}", album.getAlbumId(), e);
            throw new RuntimeException("Failed to save album to DynamoDB", e);
        }
    }

    public Album getAlbum(String albumId) {
        logger.info("Getting album from DynamoDB: albumId={}", albumId);
        try {
            Map<String, AttributeValue> key = new HashMap<>();
            key.put("albumId", AttributeValue.builder().s(albumId).build());

            GetItemRequest request = GetItemRequest.builder()
                    .tableName(tableName)
                    .key(key)
                    .build();

            GetItemResponse response = dynamoDbClient.getItem(request);

            if (!response.hasItem()) {
                logger.warn("Album not found: {}", albumId);
                return null;
            }

            Map<String, AttributeValue> item = response.item();
            Album album = new Album();
            album.setAlbumId(item.get("albumId").s());
            album.setAlbumName(item.get("albumName").s());
            album.setStatus(Album.AlbumStatus.valueOf(item.get("status").s()));
            album.setCreatedAt(Instant.parse(item.get("createdAt").s()));
            album.setExpiresAt(Long.parseLong(item.get("expiresAt").n()));

            if (item.containsKey("imageKeys")) {
                List<String> imageKeys = item.get("imageKeys").l().stream()
                        .map(AttributeValue::s)
                        .collect(Collectors.toList());
                album.setImageKeys(imageKeys);
            }

            if (item.containsKey("pdfKey")) {
                album.setPdfKey(item.get("pdfKey").s());
            }

            logger.info("Successfully retrieved album: {}", albumId);
            return album;
        } catch (Exception e) {
            logger.error("Failed to get album: {}", albumId, e);
            throw new RuntimeException("Failed to get album from DynamoDB", e);
        }
    }

    public void updateAlbumStatus(String albumId, Album.AlbumStatus status) {
        logger.info("Updating album status: albumId={}, status={}", albumId, status);
        try {
            Map<String, AttributeValue> key = new HashMap<>();
            key.put("albumId", AttributeValue.builder().s(albumId).build());

            Map<String, AttributeValueUpdate> updates = new HashMap<>();
            updates.put("status", AttributeValueUpdate.builder()
                    .value(AttributeValue.builder().s(status.name()).build())
                    .action(AttributeAction.PUT)
                    .build());

            UpdateItemRequest request = UpdateItemRequest.builder()
                    .tableName(tableName)
                    .key(key)
                    .attributeUpdates(updates)
                    .build();

            dynamoDbClient.updateItem(request);
            logger.info("Successfully updated album status: {}", albumId);
        } catch (Exception e) {
            logger.error("Failed to update album status: {}", albumId, e);
            throw new RuntimeException("Failed to update album status", e);
        }
    }
}
