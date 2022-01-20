from itertools import count
import random
import time

__all__ = [
    "retry_with_delay",
    "retry_with_exp_backoff",
]

def retry_with_exp_backoff(
    slot_time_ms,
    max_delay_ms,
    max_retries=None,
    randomize_delay=True):
  """
  Generator function that does exponential random backoff between iterations.

  There is no delay before the first iteration. For retry k, the maximum delay
  t is calculated as (2^k * unit_delay_ms) or max_delay_ms, whichever is
  smaller. If randomize_delay is true, the delay is selected randomly on the
  range [0, t] inclusive.

  Args:
    slot_time_ms (int): Delay slot time in milliseconds.
    max_delay_ms (int): Maximum delay in milliseconds.
    max_retries (int): Maximum number of retries, or None for infinite retries.
    randomize_delay (bool): Whether to randomize the delay duration.
  Yields:
    retry_num (int), starting from 0.
  """
  for retry_num in count():
    if max_retries is not None and retry_num > max_retries:
      break
    if retry_num >= 1:
      delay_ms = min(max_delay_ms, int(slot_time_ms) << (retry_num - 1))
      if randomize_delay:
        delay_ms = random.randint(0, delay_ms)
      time.sleep(delay_ms/1e3)
    yield retry_num

def retry_with_delay(
    delay_ms,
    max_retries=None):
  """
  Generator function that inserts the specified delay between iterations.
  There is no delay before the first iteration.

  Args:
    delay_ms (int): Delay time in milliseconds.
    max_retries (int): Maximum number of retries, or None for infinite retries.
  Yields:
    retry_num (int), starting from 0.
  """
  for retry_num in count():
    if max_retries is not None and retry_num > max_retries:
      break
    if retry_num >= 1:
      time.sleep(delay_ms/1e3)
    yield retry_num


def retry_meta_decorator(dec_fuc):
  def wrapper(*args, **kwargs):
    def decorator(func):
      return dec_fuc(func, *args, **kwargs)
    return decorator
  return wrapper

@retry_meta_decorator
def retry_exp_without_raising(func, slot_time_ms, max_delay_ms, max_retries):
  def retry_wrapper(self, *args, **kwargs):
    for retry_num in retry_with_exp_backoff(slot_time_ms,
                                            max_delay_ms,
                                            max_retries):
      try:
        return func(self, *args, **kwargs)
      except:
        if retry_num > max_retries:
          return None
  return retry_wrapper
