import io
import os
import pandas as pd
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

# --- SAYFA AYARLARI ---
st.set_page_config(
    page_title="Açık Uçlu Sınav Okuma Sistemi",
    page_icon="📝",
    layout="wide"
)

# --- YAPILANDIRILMIŞ ÇIKTI ŞEMASI (Pydantic) ---
class SoruDegerlendirme(BaseModel):
    soru_no: int = Field(description="Soru numarası")
    okunan_ogrenci_cevabi: str = Field(description="Kağıttan okunan el yazısı metni")
    yakalanan_kavramlar: list[str] = Field(description="Eş veya yakın anlamlı olarak doğru kullanılan hedef kavramlar")
    olumsuzluk_tuzagi_var_mi: bool = Field(description="Öğrenci kavramı kullanıp yanlış/olumsuz yargı kurduysa True")
    gerekce: str = Field(description="Kısa puanlama açıklaması")
    verilen_puan: float = Field(description="Bu sorudan alınan puan")

class KagitSonucu(BaseModel):
    ogrenci_ad_soyad: str = Field(description="Kağıdın üst kısmından okunan öğrenci adı soyadı")
    ogrenci_no: str = Field(description="Kağıttan okunan okul numarası, yoksa Belirtilmemiş")
    sorular: list[SoruDegerlendirme]
    toplam_puan: float = Field(description="Tüm soruların puan toplamı")

# --- OTURUM DURUMU (SESSION STATE) ---
if "sorular" not in st.session_state:
    st.session_state.sorular = [
        {
            "no": 1,
            "soru_metni": "Net kuvvetin sıfırdan farklı olduğu bir cismin hareket durumunu açıklayınız.",
            "kavramlar": "ivmeli hareket (5p), hız değişimi (5p)",
            "tam_puan": 10.0,
        },
        {
            "no": 2,
            "soru_metni": "Sürtünmesiz yatay düzlemde hareket eden cisme etki eden net kuvvet sıfırlanırsa ne olur?",
            "kavramlar": "sabit hızlı hareket (5p), eylemsizlik (5p)",
            "tam_puan": 10.0,
        },
    ]

if "sonuclar" not in st.session_state:
    st.session_state.sonuclar = []

# --- SIDEBAR: API ANAHTARI VE DERS BİLGİSİ ---
with st.sidebar:
    st.header("⚙️ Sistem Ayarları")
    api_key = st.secrets.get("GEMINI_API_KEY", "") or st.text_input(
        "Gemini API Anahtarı", type="password", help="Google AI Studio'dan aldığınız anahtar"
    )
    ders_adi = st.text_input("Ders Adı", value="Fizik / Ortak Yazılı")
    sinif_sube = st.text_input("Sınıf / Şube", value="10-A")
    st.divider()
    st.info(
        "**Okuma Kuralı:** Sistem tüm satırları edebi olarak yorumlamaz; "
        "cevap anahtarındaki hedef kavramları **eş/yakın anlamlı** olarak arar "
        "ve **olumsuzluk (negasyon)** tuzaklarını eler."
    )
    st.warning("⏱️ Ücretsiz yapay zeka sunucularındaki yoğunluğa göre 1 kağıdın okunması 10 ile 30 saniye arası sürebilir, lütfen çark dönerken bekleyin.")

st.title("📝 Çok Sorulu Açık Uçlu Sınav Okuma Sistemi")

tab_anahtar, tab_okuma, tab_rapor = st.tabs([
    "1️⃣ Çok Sorulu Cevap Anahtarı",
    "2️⃣ Toplu Kağıt Yükle & Oku",
    "3️⃣ Sınıf Sonuç Listesi & Excel"
])

# --- SEKME 1: CEVAP ANAHTARI OLUŞTURMA ---
with tab_anahtar:
    st.subheader("Sınav Soruları ve Hedef Kavram Rubriği")
    col_ekle, col_sil = st.columns([1, 5])
    with col_ekle:
        if st.button("➕ Yeni Soru Ekle", use_container_width=True):
            yeni_no = len(st.session_state.sorular) + 1
            st.session_state.sorular.append({
                "no": yeni_no,
                "soru_metni": "",
                "kavramlar": "",
                "tam_puan": 10.0,
            })
            st.rerun()

    for idx, s in enumerate(st.session_state.sorular):
        with st.expander(f"Soru {s['no']} ({s['tam_puan']} Puan)", expanded=True):
            c1, c2 = st.columns([4, 1])
            s["soru_metni"] = c1.text_input(
                "Soru Metni", value=s["soru_metni"], key=f"soru_{idx}"
            )
            s["tam_puan"] = c2.number_input(
                "Tam Puan", min_value=1.0, max_value=100.0, value=float(s["tam_puan"]), key=f"puan_{idx}"
            )
            s["kavramlar"] = st.text_area(
                "Aranan Anahtar Kavramlar ve Puan Ağırlıkları (Örn: istimalet (5p), hoşgörü politikası (5p))",
                value=s["kavramlar"],
                key=f"kavram_{idx}",
                height=70,
            )

# --- SEKME 2: TOPLU KAĞIT OKUMA ---
with tab_okuma:
    st.subheader("Öğrenci Sınav Kağıtlarını Toplu Yükleyin")
    yuklenen_dosyalar = st.file_uploader(
        "Sınav kağıdı fotoğraflarını seçin (JPG, PNG, WEBP - Çoklu seçim yapabilirsiniz)",
        type=["jpg", "jpeg", "png", "webp"],
        accept_multiple_files=True,
    )

    if yuklenen_dosyalar:
        st.success(f"Toplam **{len(yuklenen_dosyalar)}** adet sınav kağıdı okumaya hazır.")

        if st.button("🚀 Tüm Kağıtları Cevap Anahtarına Göre Oku & Puanla", type="primary"):
            if not api_key:
                st.error("Lütfen sol menüden Gemini API Anahtarını girin.")
            else:
                client = genai.Client(api_key=api_key)
                rubrik_metni = "\n".join([
                    f"- Soru {s['no']} (Tam Puan: {s['tam_puan']}): Soru='{s['soru_metni']}' | Aranan Kavramlar: {s['kavramlar']}"
                    for s in st.session_state.sorular
                ])

                sistem_talimati = f"""
                Sen {ders_adi} dersi için açık uçlu sınav kağıdı okuyan uzman bir değerlendiricisin.
                Görevin:
                1. Görseldeki öğrenci adı-soyadı ve numarasını oku.
                2. Her soru için öğrencinin el yazısını metne dök.
                3. Tüm metni edebi olarak uzun uzadıya analiz etmek yerine, SADECE aşağıdaki cevap anahtarında belirtilen hedef kavramları EŞ ANLAMLI veya YAKIN ANLAMLI ifadelerle yakala.
                4. KRİTİK KURAL (Negasyon Kontrolü): Öğrenci doğru kavramı yazmış ama cümlede olumsuz/yanlış bir yargı kurmuşsa (örneğin 'artmaz', 'görülmez', 'etkilemez') o kavrama KESİNLİKLE puan verme ve olumsuzluk_tuzagi_var_mi alanını True işaretle.

                CEVAP ANAHTARI:
                {rubrik_metni}
                """

                ilerleme = st.progress(0)
                yeni_sonuclar = []

                for i, dosya in enumerate(yuklenen_dosyalar):
                    with st.spinner(f"Okunuyor ({i+1}/{len(yuklenen_dosyalar)}): {dosya.name}... (Lütfen 30 saniye kadar bekleyin)"):
                        try:
                            # TURBO SIKIŞTIRMA MODÜLÜ
                            gorsel = Image.open(dosya)
                            if gorsel.mode != 'RGB':
                                gorsel = gorsel.convert('RGB')
                            gorsel.thumbnail((800, 800)) # Boyutu daha da küçülttük
                            
                            tampon_gorsel = io.BytesIO()
                            gorsel.save(tampon_gorsel, format="JPEG", quality=70) # Dosyayı minik bir JPEG'e çevirdik
                            gorsel_bytes = tampon_gorsel.getvalue()
                            
                            gorsel_part = types.Part.from_bytes(data=gorsel_bytes, mime_type="image/jpeg")

                            yanit = client.models.generate_content(
                                model="gemini-3.8-flash",
                                contents=[sistem_talimati, gorsel_part],
                                config=types.GenerateContentConfig(
                                    response_mime_type="application/json",
                                    response_schema=KagitSonucu,
                                    temperature=0.1,
                                ),
                            )
                            sonuc_obj = KagitSonucu.model_validate_json(yanit.text)
                            yeni_sonuclar.append((dosya.name, sonuc_obj))
                        except Exception as hata:
                            st.error(f"{dosya.name} okunurken hata oluştu: {hata}")

                    ilerleme.progress((i + 1) / len(yuklenen_dosyalar))

                st.session_state.sonuclar = yeni_sonuclar
                st.success("Tüm kağıtların okuması tamamlandı! Detayları aşağıda veya 3. sekmede inceleyebilirsiniz.")

    for dosya_adi, kagit in st.session_state.sonuclar:
        with st.expander(f"📄 {kagit.ogrenci_ad_soyad} (No: {kagit.ogrenci_no}) — Toplam: {kagit.toplam_puan} Puan", expanded=False):
            for s in kagit.sorular:
                st.markdown(f"**Soru {s.soru_no} ({s.verilen_puan} Puan):** {s.gerekce}")
                st.caption(f"✍️ *Okunan Yazı:* {s.okunan_ogrenci_cevabi}")
                if s.yakalanan_kavramlar:
                    st.write("✅ Yakalanan Kavramlar:", ", ".join(s.yakalanan_kavramlar))
                if s.olumsuzluk_tuzagi_var_mi:
                    st.warning("⚠️ Olumsuzluk / Yanlış Yargı Tuzağı Tespit Edildi!")

# --- SEKME 3: EXCEL RAPORU ---
with tab_rapor:
    st.subheader(f"{ders_adi} ({sinif_sube}) — Sınıf Puan Çizelgesi")
    if not st.session_state.sonuclar:
        st.info("Henüz okunmuş sınav kağıdı bulunmuyor.")
    else:
        satirlar = []
        for _, kagit in st.session_state.sonuclar:
            satir = {
                "Okul No": kagit.ogrenci_no,
                "Ad Soyad": kagit.ogrenci_ad_soyad,
            }
            for s in kagit.sorular:
                satir[f"Soru {s.soru_no}"] = s.verilen_puan
            satir["Toplam Puan"] = kagit.toplam_puan
            satirlar.append(satir)

        df = pd.DataFrame(satirlar)
        st.dataframe(df, use_container_width=True)

        tampon = io.BytesIO()
        with pd.ExcelWriter(tampon, engine="openpyxl") as yazici:
            df.to_excel(yazici, index=False, sheet_name="Sinav_Sonuclari")

        st.download_button(
            label="📥 Sonuçları Excel (.xlsx) Olarak İndir",
            data=tampon.getvalue(),
            file_name=f"{sinif_sube}_Sinav_Sonuclari.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
