import os

from dotenv import load_dotenv
load_dotenv()

from src.workers.worker_runtime import create_integrated_worker
import redis

redis_client = redis.Redis(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=0)
worker = create_integrated_worker(redis_client)
celery_app = worker.process_task.app

if __name__ == "__main__":
    celery_app.worker_main(["worker", "--loglevel=info", "--pool=solo"])
