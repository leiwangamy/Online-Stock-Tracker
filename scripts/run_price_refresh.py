from update_jobs import job_refresh_prices

print("refresh start", flush=True)
result = job_refresh_prices(max_workers=2)
print(result, flush=True)
print("refresh done", flush=True)
