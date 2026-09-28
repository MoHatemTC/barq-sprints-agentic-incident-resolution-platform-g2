import os

from dotenv import load_dotenv
load_dotenv()

import redis
from src.workers.delivery_sweep import start_delivery_sweep
from src.workers.worker_runtime import create_integrated_worker

redis_client = redis.Redis(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=0)
worker = create_integrated_worker(redis_client)
# Catches incidents whose webhook never arrived (own thread, see delivery_sweep.py).
start_delivery_sweep(redis_client)

print("Consumer started, waiting for incidents...")
worker.run(should_stop=lambda: False)
