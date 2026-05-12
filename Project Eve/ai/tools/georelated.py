import requests
import numpy as np
import os
from PIL import Image
from io import BytesIO
import cv2

SAVE_DIR = "/data/pics"


def _amap_key() -> str:
    return os.getenv("AMAP_MCP_TOKEN", "")

def latlon_to_amap_coord(lat, lon):
    convert_url = f"https://restapi.amap.com/v3/assistant/coordinate/convert?locations={lon},{lat}&coordsys=gps&output=json&key={_amap_key()}"
    try:
        resp = requests.get(convert_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1" and data.get("locations"):
            coords = data["locations"].split(",")
            amap_lon = float(coords[0])
            amap_lat = float(coords[1])
            #print(f"坐标转换成功: GPS({lon},{lat}) -> 高德({amap_lon},{amap_lat})")
            return amap_lon, amap_lat
        else:
            print(f"坐标转换失败，使用原坐标: {data.get('info')}")
            return lon, lat
    except Exception as e:
        print(f"坐标转换API调用失败，使用原坐标: {e}")
        return lon, lat

def format_heading(heading):
    try:
        h = float(heading) % 360
    except:
        return "未知"

    # 四个主方向
    directions = {
        "北": 0,
        "东": 90,
        "南": 180,
        "西": 270
    }

    # 计算与每个方向的最小角度差
    def angle_diff(a, b):
        diff = abs(a - b)
        return min(diff, 360 - diff)

    # 找最近主方向
    main_dir = min(directions.keys(), key=lambda d: angle_diff(h, directions[d]))
    main_angle = directions[main_dir]

    # 偏差角
    diff = (h - main_angle + 360) % 360
    if diff > 180:
        diff -= 360  # 转成 [-180, 180]

    # 接近正方向（±5°）
    if abs(diff) <= 5:
        return f"正{main_dir}"

    # 判断偏向哪个方向
    if main_dir == "北":
        side = "东" if diff > 0 else "西"
    elif main_dir == "东":
        side = "南" if diff > 0 else "北"
    elif main_dir == "南":
        side = "西" if diff > 0 else "东"
    elif main_dir == "西":
        side = "北" if diff > 0 else "南"

    return f"{main_dir}偏{side} {abs(diff):.0f}°"


def gps_to_amap_and_reverse(lat, lon):
    """
    将 GPS 坐标转换为高德坐标并逆向获取地址
    :param lat: 纬度
    :param lon: 经度
    :param amap_key: 高德 Web API key
    :return: 地址字符串 或 None
    """

    # 1️⃣ 坐标转换：GPS -> 高德坐标
    convert_url = "https://restapi.amap.com/v3/assistant/coordinate/convert"
    convert_params = {
        "locations": f"{lon},{lat}",  # 经度在前，纬度在后
        "coordsys": "gps",
        "output": "JSON",
        "key": _amap_key()
    }

    convert_resp = requests.get(convert_url, params=convert_params)
    convert_data = convert_resp.json()

    if convert_data.get("status") != "1":
        print("坐标转换失败:", convert_data.get("info"))
        return None

    converted_locations = convert_data.get("locations")  # 格式: "116.481499,39.990475"
    conv_lon, conv_lat = map(float, converted_locations.split(","))

    # 2️⃣ 逆地理编码：高德坐标 -> 地址
    geocode_url = "https://restapi.amap.com/v3/geocode/regeo"
    geocode_params = {
        "location": f"{conv_lon},{conv_lat}",
        "key": _amap_key(),
        "radius": 1000,
        "extensions": "all",
        "output": "JSON"
    }

    geocode_resp = requests.get(geocode_url, params=geocode_params)
    geocode_data = geocode_resp.json()

    if geocode_data.get("status") != "1":
        print("逆地理编码失败:", geocode_data.get("info"))
        return None

    address = geocode_data['regeocode']['formatted_address']
    return address


def draw_navigation_arrow(img, direction):
    """
    在图像中心绘制导航箭头（类似高德地图导航样式），指向指定方向
    img: RGBA图像数组
    direction: 方向角度（0-359），0表示正北，顺时针旋转
    """
    h, w = img.shape[:2]
    center_x, center_y = w // 2, h // 2
    
    
    # 2. 绘制三角形箭头（类似高德导航）
    # 三角形尺寸
    triangle_height = 24  # 三角形高度
    triangle_base = 20    # 三角形底边宽度
    
    # 三角形坐标（以原点为中心，向上指）
    triangle_points = np.array([
        [0, -triangle_height * 0.6],              # 顶点
        [-triangle_base / 2, triangle_height * 0.4],   # 左下
        [triangle_base / 2, triangle_height * 0.4],    # 右下
    ], dtype=np.float32)
    
    # 将方向转换为弧度（0度为正北）
    angle_rad = np.radians(direction)
    
    # 旋转矩阵
    rotation_matrix = np.array([
        [np.cos(angle_rad), -np.sin(angle_rad)],
        [np.sin(angle_rad), np.cos(angle_rad)]
    ])
    
    # 旋转三角形坐标
    rotated_triangle = triangle_points @ rotation_matrix.T
    
    # 平移到图像中心
    rotated_triangle += [center_x, center_y]
    rotated_triangle = rotated_triangle.astype(np.int32)
    
    # 绘制三角形（蓝色填充）
    cv2.fillPoly(img, [rotated_triangle], (30, 144, 255, 255))  # 道奇蓝
    
    # 绘制三角形边框（深蓝色，使其更清晰）
    cv2.polylines(img, [rotated_triangle], True, (0, 100, 200, 255), 2)
    
    return img


def save_base_map_only(amap_lon, amap_lat, zoom=12, size=512, direction=None):
    """
    下载并保存纯高德地图底图，不叠加任何图层，可选在中心添加导航标志
    amap_lon, amap_lat: 高德坐标系的中心点
    zoom: 缩放级别
    size: 图片尺寸
    direction: 方向角度（0-359），0表示正北，90表示正东，None表示不绘制导航标志
    """
    os.makedirs(SAVE_DIR, exist_ok=True)
    
    # 下载高德静态地图
    amap_url = f"https://restapi.amap.com/v3/staticmap?location={amap_lon},{amap_lat}&zoom={zoom}&size={size}*{size}&key={_amap_key()}"
    
    try:
        resp = requests.get(amap_url, timeout=10)
        resp.raise_for_status()
        base_img = np.array(Image.open(BytesIO(resp.content)).convert("RGBA"))
        
        # 如果指定了方向，绘制导航车头标志
        if direction is not None:
            base_img = draw_navigation_arrow(base_img, direction)
        
        # 保存图片
        path = os.path.join(SAVE_DIR, f"base_map_zoom{zoom}.png")
        cv2.imwrite(path, base_img)
        print(f"纯底图保存成功 (ZOOM={zoom}): {path}")
        return path
    except Exception as e:
        print(f"下载高德地图失败 (ZOOM={zoom}): {e}")
        return ""


def getBaseImage(amap_lat,amap_lon,size,ZOOM=7):
    amap_url = f"https://restapi.amap.com/v3/staticmap?location={amap_lon},{amap_lat}&zoom={ZOOM}&size={size}*{size}&key={_amap_key()}"
    try:
        resp = requests.get(amap_url, timeout=10)
        resp.raise_for_status()
        base_img = np.array(Image.open(BytesIO(resp.content)).convert("RGBA"))
        print(f"高德地图底图下载成功 (ZOOM={ZOOM})")
    except Exception as e:
        print(f"下载高德地图失败: {e}")
        base_img = np.zeros((size, size, 4), dtype=np.uint8)
        base_img[:, :, :] = 200  # 浅灰色备用
    return base_img
