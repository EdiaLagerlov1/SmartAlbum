package com.smartalbum.common;

public class Config {
    public static final String BUCKET_NAME = "smartalbum-photos-bucket";
    public static final String TABLE_NAME = "SmartAlbum-Albums";
    public static final String REGION = "us-east-1";
    public static final long MAX_IMAGE_SIZE = 5 * 1024 * 1024; // 5MB
    public static final int MAX_IMAGES = 50;
    public static final String[] ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/jpg"};
}
