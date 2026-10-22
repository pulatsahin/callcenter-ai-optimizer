
# Outbound Kampanya Operasyon Politikası

Dokuman Kodu: CC-POL-001
Surum: 1.0
Kapsam: Ornek outbound IVR kampanyalari

## Kampanya calisma saatleri
- Ornek kampanyalar hafta ici 09:00-18:00 arasinda calisir.
- Calisma saatleri disinda otomatik arama baslatilmaz.
- Kapasite hesaplarinda gunluk calisma penceresi dikkate alinmalidir.

## Port yonetimi
- Toplam simulasyon port kapasitesi 30'dur.
- Bir kampanyaya port atanirken diger kampanyalarin
  ihtiyaclari da degerlendirilmelidir.
- Port artisi icin operasyon yoneticisi onayi gerekir.
- AI Agent port sayisini dogrudan degistiremez.

## Kapasite tahmini
- Cevaplanan ve cevaplanmayan aramalar farkli surelerde
  port isgal edebilir.
- Arama kurulumu ve kapanis sureleri hesaplamaya eklenir.
- Portlarin surekli dolu oldugu varsayimi gercekci
  olmayan iyimser tahminlere neden olabilir.
- Tekrar aramalar toplam deneme sayisini artirabilir.

## Raporlama
- Kampanya adi, kalan kayit, port sayisi, cevaplanma orani
  ve tahmini tamamlanma suresi raporlanir.
- Oneriler gerceklesmis islem olarak sunulmaz.
