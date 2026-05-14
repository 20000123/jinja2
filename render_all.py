# -*- coding: utf-8 -*-
"""
交换机配置统一渲染脚本
根据交换机的type和model自动选择对应的模板生成配置文件
支持从API或本地文件获取数据
"""
import json
import os
import re
import sys
from jinja2 import Environment, FileSystemLoader
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ===================== 配置 =====================
# 默认订单ID，可以通过命令行参数覆盖
DEFAULT_ORDER_ID = "23711"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
#DEFAULT_DATA_FILE = "ID.json"
API_BASE_URL = "https://rtree.ksyun.com/kscrcdn_api/v1/workflow/order_raw/"

# ===================== 自定义 Jinja2 过滤器 =====================
def ip_wildcard(ip_block):
    """将 CIDR 格式的 IP 转换为 IP+反掩码格式，如 1.2.3.0/24 -> 1.2.3.0 0.0.0.255"""
    if not ip_block or "/" not in ip_block:
        return ip_block or ""
    addr, prefix_len = ip_block.split("/")
    prefix_len = int(prefix_len)
    mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
    wildcard = (~mask) & 0xFFFFFFFF
    wildcard_str = '.'.join(
        str((wildcard >> (8 * i)) & 0xFF) for i in reversed(range(4))
    )
    return f"{addr} {wildcard_str}"

# ===================== 加载模板环境 =====================
env = Environment(
    loader=FileSystemLoader(BASE_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    extensions=['jinja2.ext.do']
)
env.filters['ip_wildcard'] = ip_wildcard

# ===================== 定义常用变量 =====================
def build_common_vars(data):
    """从数据中提取模板常用变量，避免每个模板重复定义"""
    api_json = data["api_json"]
    new_switch_list = api_json.get("new_switch_list", [])
    node_list = api_json.get("node_list", [])

    # 获取 in/out/mgt 交换机
    def find_switch(switch_type):
        for sw in new_switch_list:
            if sw.get("type") == switch_type:
                return sw
        return {}

    in_switch = find_switch("in")
    out_switch = find_switch("out")
    mg_switch = find_switch("mgt")

    # 获取 node_list[0].isp[0] 的 ip_block 和反掩码
    isp0 = {}
    ip_block_wildcard = ""
    if node_list:
        isp_list = node_list[0].get("isp", [])
        if isp_list:
            isp0 = isp_list[0]
            ip_block = isp0.get("ip_block", "")
            if ip_block and "/" in ip_block:
                addr, prefix_len = ip_block.split("/")
                prefix_len = int(prefix_len)
                mask = (0xFFFFFFFF << (32 - prefix_len)) & 0xFFFFFFFF
                wildcard = (~mask) & 0xFFFFFFFF
                wildcard_str = '.'.join(
                    str((wildcard >> (8 * i)) & 0xFF) for i in reversed(range(4))
                )
                ip_block_wildcard = f"{addr} {wildcard_str}"

    return {
        "in_switch": in_switch,
        "in_hostname": in_switch.get("hostname", "未找到"),
        "in_sn": in_switch.get("sn", ""),
        "out_switch": out_switch,
        "out_hostname": out_switch.get("hostname", "未找到"),
        "out_sn": out_switch.get("sn", ""),
        "mg_switch": mg_switch,
        "mg_hostname": mg_switch.get("hostname", "未找到"),
        "isp0": isp0,
        "ip_block_wildcard": ip_block_wildcard,
    }

# ===================== 从API获取数据 =====================
def fetch_data_from_api(order_id):
    """从API接口获取数据"""
    if not HAS_REQUESTS:
        print(f"❌ 缺少requests库，请先安装：pip install requests")
        print(f"   或者使用本地数据文件：python {os.path.basename(__file__)}")
        return None

    url = f"{API_BASE_URL}{order_id}"
    print(f"🌐 正从API获取数据：{url}")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"❌ API请求失败：{e}")
        return None

# ===================== 从本地文件加载数据 =====================
def load_data_from_file(file_path):
    """从本地文件加载JSON数据"""
    if not os.path.exists(file_path):
        print(f"❌ 数据文件不存在：{file_path}")
        return None

    print(f"📂 正从本地文件加载数据：{file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ JSON解析失败：{e}")
            return None

# ===================== 加载数据 =====================
def get_data(order_id=None):
    """获取数据，优先使用API，否则使用本地文件"""
    if order_id:
        # 使用API获取数据
        data = fetch_data_from_api(order_id)
        if data:
            return data
        print(f"⚠️  API获取失败，尝试使用本地数据文件...\n")

    # 使用本地文件
    data_file = os.path.join(BASE_DIR, DEFAULT_DATA_FILE)
    data = load_data_from_file(data_file)
    if not data:
        print(f"\n💡 使用方法：")
        print(f"   1. 从API获取：python {os.path.basename(__file__)} <订单ID>")
        print(f"   2. 使用本地文件：python {os.path.basename(__file__)}")
        sys.exit(1)

    return data

# ===================== 解析命令行参数 =====================
if len(sys.argv) > 1:
    order_id = sys.argv[1]
else:
    order_id = DEFAULT_ORDER_ID

# ===================== 加载数据 =====================
data = get_data(order_id)

# ===================== 获取交换机列表 =====================
switch_list = data["data"]["api_json"]["new_switch_list"]

# ===================== 遍历交换机，生成配置文件 =====================
generated_files = []
for switch in switch_list:
    switch_type = switch.get("type")

    # 只处理in和out类型的交换机，忽略mgt
    if switch_type not in ["in", "out"]:
        continue

    model = switch.get("model")
    sn = switch.get("sn")

    # 检查必要字段
    if not model or not sn:
        print(f"⚠️ 跳过交换机：type={switch_type}, 缺少model或sn信息")
        continue

    # 构建模板文件名：model-type.j2
    template_name = f"{model}-{switch_type}.j2"

    # 检查模板是否存在
    if not os.path.exists(os.path.join(BASE_DIR, template_name)):
        print(f"⚠️ 跳过交换机：type={switch_type}, model={model}, 模板文件不存在：{template_name}")
        continue

    # 加载模板
    try:
        template = env.get_template(template_name)
    except Exception as e:
        print(f"❌ 模板加载失败：{template_name}, 错误：{e}")
        continue

    # 渲染配置
    try:
        common = build_common_vars(data["data"])
        config = template.render(data=data["data"], **common)

        # 清理多余的空行
        config = re.sub(r'\n\s*\n+', '\n', config)
    except Exception as e:
        print(f"❌ 渲染失败：type={switch_type}, model={model}, sn={sn}, 错误：{e}")
        continue

    # 生成配置文件，文件名为sn.cfg
    output_file = f"{sn}.cfg"
    output_path = os.path.join(BASE_DIR, output_file)

    # 写入文件
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(config)
        generated_files.append(output_path)
        print(f"✅ 生成成功：{output_file} (type={switch_type}, model={model})")
    except Exception as e:
        print(f"❌ 文件写入失败：{output_file}, 错误：{e}")

# ===================== 输出汇总信息 =====================
print("\n" + "="*60)
print(f"📊 渲染完成！共生成 {len(generated_files)} 个配置文件：")
print("="*60)
for file_path in generated_files:
    file_name = os.path.basename(file_path)
    print(f"  - {file_name}")
print("="*60)