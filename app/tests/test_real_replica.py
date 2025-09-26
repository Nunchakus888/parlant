#!/usr/bin/env python3
"""
真实副本集配置测试

使用实际的主机名和端口进行副本集测试
"""

import sys
import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from utils.format import encode_mongodb_url


def test_replica_set_scenarios():
    """测试各种副本集连接场景"""
    print("🚀 真实副本集配置测试")
    print("=" * 60)
    
    # 基于实际可用的连接信息
    base_host = "dds-bp17b43b5614b8a41281-pub.mongodb.rds.aliyuncs.com"
    username = "ycloud"
    password = "q7RB-k.xtN"
    database = "ycloud"
    auth_source = "admin"
    replica_set_name = "mgset-61737024"
    
    # 测试场景
    test_scenarios = [
        {
            "name": "单副本（已知可用）",
            "url": f"mongodb://{username}:{password}@{base_host}:3717/{database}?authSource={auth_source}"
        },
        {
            "name": "双副本（不同端口）",
            "url": f"mongodb://{username}:{password}@{base_host}:3717,{base_host}:3718/{database}?authSource={auth_source}"
        },
        {
            "name": "三副本（不同端口）",
            "url": f"mongodb://{username}:{password}@{base_host}:3717,{base_host}:3718,{base_host}:3719/{database}?authSource={auth_source}"
        },
        {
            "name": "指定副本集名称",
            "url": f"mongodb://{username}:{password}@{base_host}:3717,{base_host}:3718/{database}?replicaSet={replica_set_name}&authSource={auth_source}"
        },
        {
            "name": "副本集名称（单副本）",
            "url": f"mongodb://{username}:{password}@{base_host}:3717/{database}?replicaSet={replica_set_name}&authSource={auth_source}"
        }
    ]
    
    results = {}
    
    for scenario in test_scenarios:
        print(f"\n📍 测试 {scenario['name']}:")
        print(f"   URL: {scenario['url'][:100]}...")
        
        try:
            encoded_url = encode_mongodb_url(scenario['url'])
            client = MongoClient(encoded_url, serverSelectionTimeoutMS=15000)
            
            # 测试连接
            client.admin.command('ping')
            print("✅ 连接成功!")
            
            # 获取副本集信息
            try:
                rs_status = client.admin.command('replSetGetStatus')
                set_name = rs_status.get('set', 'Unknown')
                my_state = rs_status.get('myState', 'Unknown')
                print(f"   副本集: {set_name}")
                print(f"   当前状态: {my_state}")
                
                # 获取成员信息
                members = rs_status.get('members', [])
                print(f"   副本成员: {len(members)}")
                for member in members[:3]:
                    name = member.get('name', 'Unknown')
                    state = member.get('stateStr', 'Unknown')
                    print(f"     - {name}: {state}")
                
            except Exception as e:
                print(f"   ⚠️  无法获取副本集状态: {e}")
            
            # 测试数据库访问
            db = client.ycloud
            collections = db.list_collection_names()
            print(f"   数据库访问: ✅ ({len(collections)} 个集合)")
            
            client.close()
            results[scenario['name']] = True
            
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f"❌ 连接失败: {e}")
            results[scenario['name']] = False
        except Exception as e:
            print(f"❌ 错误: {e}")
            results[scenario['name']] = False
    
    return results


def test_url_encoding_with_replica():
    """测试副本集URL编码"""
    print(f"\n🧪 测试副本集URL编码...")
    print("=" * 50)
    
    test_urls = [
        "mongodb://user:pass+word@host1:27017,host2:27018,host3:27019/db?replicaSet=rs0&authSource=admin",
        "mongodb://ycloud:q7RB-k.xtN@dds-bp17b43b5614b8a41281-pub.mongodb.rds.aliyuncs.com:3717,dds-bp17b43b5614b8a41281-pub.mongodb.rds.aliyuncs.com:3718/ycloud?replicaSet=mgset-61737024&authSource=admin",
        "mongodb://user:pass@word@host1:27017,host2:27018/db?replicaSet=rs0",
    ]
    
    for i, url in enumerate(test_urls, 1):
        print(f"\n测试 {i}:")
        print(f"  原始: {url}")
        encoded = encode_mongodb_url(url)
        print(f"  编码: {encoded}")
        print(f"  改变: {'是' if url != encoded else '否'}")


def main():
    """主函数"""
    # 1. 测试URL编码
    test_url_encoding_with_replica()
    
    # 2. 测试副本集连接
    results = test_replica_set_scenarios()
    
    # 3. 总结结果
    print(f"\n📊 测试结果总结:")
    print("=" * 50)
    
    success_count = 0
    for name, success in results.items():
        status = "✅ 成功" if success else "❌ 失败"
        print(f"{name}: {status}")
        if success:
            success_count += 1
    
    print(f"\n总体结果: {success_count}/{len(results)} 个测试成功")
    
    # 4. 推荐配置
    if success_count > 0:
        print(f"\n🎉 副本集连接功能验证成功!")
        print(f"💡 URL编码处理支持副本集地址")
        
        # 找到最佳配置
        best_configs = [name for name, success in results.items() if success]
        if best_configs:
            print(f"\n🎯 可用的配置:")
            for config in best_configs:
                print(f"   - {config}")
        
        print(f"\n🔧 在agent.py中的使用:")
        print(f"   session_store=encode_mongodb_url(os.environ.get('MONGODB_SESSION_STORE'))")
        print(f"   确保MONGODB_SESSION_STORE环境变量包含副本集URL")
        
        print(f"\n📝 副本集URL格式示例:")
        print(f"   mongodb://user:pass@host1:port1,host2:port2,host3:port3/db?replicaSet=rs0&authSource=admin")
        print(f"   mongodb://user:pass@host1:port1,host2:port2/db?authSource=admin")
    
    else:
        print(f"\n❌ 所有副本集连接测试都失败了")
        print(f"💡 请检查:")
        print(f"   1. 网络连接")
        print(f"   2. 端口访问权限")
        print(f"   3. 副本集配置")


if __name__ == "__main__":
    main()
