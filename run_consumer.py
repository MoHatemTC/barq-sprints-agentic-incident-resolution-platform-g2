from dotenv import load_dotenv
load_dotenv()

import redis
from src.workers.worker_runtime import create_integrated_worker

redis_client = redis.Redis(host="localhost", port=6379, db=0)
worker = create_integrated_worker(redis_client)

print("Consumer started, waiting for incidents...")
worker.run(should_stop=lambda: False)
