import os

from dotenv import load_dotenv
load_dotenv()

import redis
from src.workers.worker_runtime import create_integrated_worker

redis_client = redis.Redis(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=0)
worker = create_integrated_worker(redis_client)

print("Consumer started, waiting for incidents...")
worker.run(should_stop=lambda: False)
