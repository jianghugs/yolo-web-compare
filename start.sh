#!/bin/bash
# 接口对比工具启动脚本（端口 1239）
cd /home/deployer/yolo_web_compare
nohup /home/deployer/miniconda3/envs/yolo11/bin/python -u server.py > service.log 2>&1 &
echo "started pid $!"
