package com.smartalbum.common;

import java.time.Instant;
import java.util.List;

public class Album {
    private String albumId;
    private String albumName;
    private List<String> imageKeys;
    private String pdfKey;
    private AlbumStatus status;
    private Instant createdAt;
    private Long expiresAt;

    public Album() {
    }

    public Album(String albumId, String albumName) {
        this.albumId = albumId;
        this.albumName = albumName;
        this.status = AlbumStatus.UPLOADING;
        this.createdAt = Instant.now();
        this.expiresAt = Instant.now().getEpochSecond() + (7 * 24 * 60 * 60);
    }

    public String getAlbumId() {
        return albumId;
    }

    public void setAlbumId(String albumId) {
        this.albumId = albumId;
    }

    public String getAlbumName() {
        return albumName;
    }

    public void setAlbumName(String albumName) {
        this.albumName = albumName;
    }

    public List<String> getImageKeys() {
        return imageKeys;
    }

    public void setImageKeys(List<String> imageKeys) {
        this.imageKeys = imageKeys;
    }

    public String getPdfKey() {
        return pdfKey;
    }

    public void setPdfKey(String pdfKey) {
        this.pdfKey = pdfKey;
    }

    public AlbumStatus getStatus() {
        return status;
    }

    public void setStatus(AlbumStatus status) {
        this.status = status;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Instant createdAt) {
        this.createdAt = createdAt;
    }

    public Long getExpiresAt() {
        return expiresAt;
    }

    public void setExpiresAt(Long expiresAt) {
        this.expiresAt = expiresAt;
    }

    public enum AlbumStatus {
        UPLOADING, PROCESSING, READY, FAILED
    }
}
