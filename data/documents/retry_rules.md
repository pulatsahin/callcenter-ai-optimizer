
# Outbound Retry Politikasi

Dokuman Kodu: CC-POL-002
Surum: 1.0
Kapsam: Ornek outbound IVR kampanyalari

## Cevapsiz aramalar
- NO_ANSWER sonucu alan kayitlar tekrar arama adayi olabilir.
- Ornek bekleme suresi en az 60 dakikadir.
- Ornek gunluk maksimum deneme sayisi 3'tur.

## Mesgul aramalar
- BUSY sonucu alan kayitlar tekrar arama adayi olabilir.
- Ornek bekleme suresi en az 30 dakikadir.
- Gunluk deneme limiti asilmamalidir.

## Basarili aramalar
- Basarili tamamlanan kayitlar tekrar aranmaz.

## Gecersiz numaralar
- INVALID_NUMBER sonucu alan kayitlar otomatik
  tekrar arama listesinden cikarilir.

## Genel kurallar
- Retry islemleri kampanya calisma saatleri icinde yapilir.
- Musteri tercihleri ve aranmama talepleri onceliklidir.
- AI Agent retry kurallarini analiz edebilir fakat
  gercek arama emri olusturamaz.
