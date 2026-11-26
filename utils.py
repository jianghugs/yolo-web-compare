# -*- coding: utf-8 -*-
"""纯函数工具集：方便单元测试（不依赖 torch/ultralytics）。"""
import os

import cv2
import numpy as np


def is_safe_rtmp_url(url):
    """只允许 rtmp:// 协议（防 SSRF），返回规范化后的地址或 None。"""
    if not url or not url.strip().lower().startswith('rtmp://'):
        return None
    return url.strip()


def people_from_name(fname):
    """从文件名 detect_20261029_095812_2p.jpg 解析检出人数；解析失败返回 -1。"""
    try:
        return int(os.path.splitext(fname)[0].rsplit('_', 1)[1].split('p')[0])
    except (ValueError, IndexError):
        return -1


def dhash(img, hash_size=16):
    """dHash 感知哈希：相邻像素亮度比较，返回整数位串。"""
    resized = cv2.resize(img, (hash_size + 1, hash_size))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    diff = gray[:, 1:] > gray[:, :-1]
    return int.from_bytes(np.packbits(diff).tobytes(), 'big')


def hamming(h1, h2):
    """两个哈希的汉明距离。"""
    return bin(h1 ^ h2).count('1')
