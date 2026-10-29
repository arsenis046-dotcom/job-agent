"""Агенты: промпт + схема входа/выхода на каждого.

Появляются по фазам (раздел 9 ТЗ):
  Фаза 1 — profile_builder, resume_writer
  Фаза 2 — scout, matcher
  Фаза 3 — letter_writer, fact_checker
  Фаза 4 — market_analyst, portfolio_advisor, interview_coach

Общее правило: факты берутся только из profile/master_profile.yaml,
вывод — структурированный (tool use), Fact Checker вызывается с чистым
контекстом перед любой отправкой.
"""
