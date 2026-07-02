"""
RabbitMQ 连接测试脚本
用于验证 RabbitMQ 配置是否正确
"""
import sys


def test_rabbitmq_connection():
    """测试 RabbitMQ 连接"""
    print("=" * 60)
    print("RabbitMQ 连接测试")
    print("=" * 60)
    
    try:
        from kombu import Connection
        from app.core.config import settings
        
        broker_url = settings.CELERY_BROKER_URL
        print(f"\n📡 Broker URL: {broker_url}")
        
        # 尝试连接
        print("\n🔌 正在连接到 RabbitMQ...")
        conn = Connection(broker_url)
        conn.ensure_connection(max_retries=3, timeout=5)
        
        print("✅ 连接成功！")
        
        # 获取连接信息
        print(f"\n📊 连接详情:")
        print(f"   Host: {conn.hostname}")
        print(f"   Port: {conn.port}")
        print(f"   Virtual host: {conn.virtual_host}")
        
        conn.release()
        
        return True
        
    except ImportError as e:
        print(f"\n❌ 导入错误: {e}")
        print("\n💡 请运行: pip install -r requirements.txt")
        return False
        
    except Exception as e:
        print(f"\n❌ 连接失败: {e}")
        print("\n💡 可能的原因:")
        print("   1. RabbitMQ 未启动")
        print("   2. 端口 5672 被占用")
        print("   3. 用户名/密码错误")
        print("   4. 防火墙阻止连接")
        print("\n🔧 解决方案:")
        print("   Docker: docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management")
        print("   管理界面: http://localhost:15672 (guest/guest)")
        return False


def test_celery_app():
    """测试 Celery 应用配置"""
    print("\n" + "=" * 60)
    print("Celery 应用配置测试")
    print("=" * 60)
    
    try:
        from app.core.celery import celery_app
        
        print(f"\n📱 Celery App: {celery_app.main}")
        print(f"📤 Broker: {celery_app.conf.broker_url}")
        print(f"📥 Backend: {celery_app.conf.result_backend}")
        print(f"🕐 Timezone: {celery_app.conf.timezone}")
        print(f"📝 Serializer: {celery_app.conf.task_serializer}")
        
        # 检查任务注册
        print(f"\n📋 已注册的任务:")
        registered_tasks = list(celery_app.tasks.keys())
        for task in registered_tasks:
            if not task.startswith('celery.'):
                print(f"   ✓ {task}")
        
        print("\n✅ Celery 配置正确！")
        return True
        
    except Exception as e:
        print(f"\n❌ Celery 配置错误: {e}")
        return False


def main():
    """主函数"""
    print("\n🚀 开始 RabbitMQ 和 Celery 配置验证...\n")
    
    # 测试 RabbitMQ 连接
    rabbitmq_ok = test_rabbitmq_connection()
    
    # 测试 Celery 配置
    celery_ok = test_celery_app()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    if rabbitmq_ok and celery_ok:
        print("\n✅ 所有测试通过！系统已准备好使用 RabbitMQ。")
        print("\n📖 下一步:")
        print("   1. 启动 Celery worker: celery -A app.core.celery worker --loglevel=info")
        print("   2. 访问管理界面: http://localhost:15672")
        print("   3. 查看详细文档: RABBITMQ_SETUP_GUIDE.md")
        return 0
    else:
        print("\n❌ 部分测试失败，请检查上述错误信息。")
        if not rabbitmq_ok:
            print("\n⚠️  RabbitMQ 连接问题:")
            print("   - 确保 RabbitMQ 正在运行")
            print("   - 检查 .env 中的 CELERY_BROKER_URL 配置")
        if not celery_ok:
            print("\n⚠️  Celery 配置问题:")
            print("   - 检查依赖是否安装: pip install -r requirements.txt")
            print("   - 查看错误日志获取详细信息")
        return 1


if __name__ == "__main__":
    sys.exit(main())
