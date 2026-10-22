
def simulate_retries(
    records: int,
    answer_rate: float,
    max_attempts: int = 3,
    retry_fraction: float = 1.0,
) -> dict:
    """
    Her denemede sabit cevaplanma olasiligi varsayar.
    Basarisiz kayitlarin retry_fraction kadari
    yeniden arama adayi olur.

    Bu fonksiyon zamanlama yapmaz; toplam deneme
    yukunun beklenen degerini hesaplar.
    """

    if records < 0:
        raise ValueError("Kayit sayisi negatif olamaz.")

    if not 0 <= answer_rate <= 100:
        raise ValueError("Cevaplanma orani 0-100 olmali.")

    if max_attempts < 1:
        raise ValueError("En az bir deneme olmali.")

    if not 0 <= retry_fraction <= 1:
        raise ValueError("Retry orani 0-1 arasinda olmali.")

    p = answer_rate / 100
    remaining = float(records)
    total_attempts = 0.0
    attempts_by_round = []

    for attempt_no in range(1, max_attempts + 1):
        attempts_by_round.append({
            "attempt": attempt_no,
            "expected_calls": round(remaining, 2),
        })

        total_attempts += remaining

        remaining = remaining * (1 - p) * retry_fraction

    
    expected_answers = 0.0
    current_calls = float(records)

    for _ in range(max_attempts):
        expected_answers += current_calls * p
        current_calls *= (1 - p) * retry_fraction

    expected_unanswered_final_round = (
        attempts_by_round[-1]["expected_calls"] * (1 - p)
        if attempts_by_round else 0
    )

    return {
        "records": records,
        "answer_rate": answer_rate,
        "max_attempts": max_attempts,
        "retry_fraction": retry_fraction,
        "expected_total_attempts": round(total_attempts, 2),
        "expected_answers": round(expected_answers, 2),
        "expected_unanswered_after_final_attempt": round(
            expected_unanswered_final_round, 2
        ),
        "attempts_by_round": attempts_by_round,
        "note": (
            "Beklenen deger modelidir. BUSY/NO_ANSWER ayrimi, "
            "retry bekleme suresi ve calisma saatleri uygulanmaz. "
            "Retry disi birakilan kayitlar ayrica raporlanmaz."
        ),
    }

