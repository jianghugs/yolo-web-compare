# -*- coding: utf-8 -*-
"""utils.py 单元测试：URL 校验、人数解析、dHash。"""
import os
import sys

import numpy as np
import pytest
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils


# ---------------------------------------------------------------- URL 校验
def test_safe_rtmp_url():
    assert utils.is_safe_rtmp_url('rtmp://host/live/key') == 'rtmp://host/live/key'
    assert utils.is_safe_rtmp_url('  RTMP://host/live  ') == 'RTMP://host/live'
    assert utils.is_safe_rtmp_url('http://host/live') is None
    assert utils.is_safe_rtmp_url('https://host') is None
    assert utils.is_safe_rtmp_url('') is None
    assert utils.is_safe_rtmp_url(None) is None
    assert utils.is_safe_rtmp_url('rtmp://evil@127.0.0.1/x') == 'rtmp://evil@127.0.0.1/x'


# ---------------------------------------------------------------- 人数解析
def test_people_from_name():
    assert utils.people_from_name('detect_20261029_095812_2p.jpg') == 2
    assert utils.people_from_name('detect_20261029_095812_0p.jpg') == 0
    assert utils.people_from_name('detect_20261029_095812_12p.jpg') == 12
    assert utils.people_from_name('detect_nope.jpg') == -1
    assert utils.people_from_name('latest.jpg') == -1
    assert utils.people_from_name('') == -1


# ---------------------------------------------------------------- dHash
def _img(seed, size=(96, 96)):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, size=(size[1], size[0], 3), dtype=np.uint8)


def test_dhash_same_image():
    assert utils.dhash(_img(42)) == utils.dhash(_img(42))


def test_hamming_identical_zero():
    h = utils.dhash(_img(1))
    assert utils.hamming(h, h) == 0


def test_hamming_different_large():
    h1 = utils.dhash(_img(1))
    h2 = utils.dhash(_img(999))
    assert utils.hamming(h1, h2) > 40


def test_dhash_stable_under_jpeg(tmp_path):
    """dHash 对 JPEG 有损压缩应保持稳定（距离很小）。"""
    img = _img(7)
    p = str(tmp_path / 'a.jpg')
    cv2.imwrite(p, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    reloaded = cv2.imread(p)
    assert utils.hamming(utils.dhash(img), utils.dhash(reloaded)) <= 10
