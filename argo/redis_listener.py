import os
import django
import redis
import json
import logging

# Initialize Django ORM context using argo settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings_argo")
django.setup()

# Configure Logger (uses Django's logging system if configured, fallback to basicConfig)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("argo.redis_listener")

def main():
    # Connect to local Redis
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    
    r = redis.Redis(host=redis_host, port=redis_port, db=0)
    pubsub = r.pubsub()
    pubsub.subscribe("nautilus_events")
    
    logger.info(f"Successfully subscribed to 'nautilus_events' on Redis {redis_host}:{redis_port}")
    
    for message in pubsub.listen():
        if message["type"] == "message":
            try:
                data = json.loads(message["data"].decode("utf-8"))
                logger.info(f"Intercepted Redis event: {data}")
                
                # Place any Django model creation or database operations here, e.g.:
                # DjangoPosition.objects.create(...)
            except Exception as e:
                logger.error(f"Error parsing Redis message: {e}")

if __name__ == "__main__":
    main()