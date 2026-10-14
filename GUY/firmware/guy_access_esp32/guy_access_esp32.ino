
#include <Arduino.h>
#include <SPI.h>
#include <MFRC522.h>
#include <Adafruit_Fingerprint.h>
#include <ArduinoJson.h>

constexpr uint8_t RFID_SS = 5;
constexpr uint8_t RFID_RST = 22;
constexpr uint8_t FP_RX = 16;
constexpr uint8_t FP_TX = 17;

constexpr bool ENABLE_RELAY = false;
constexpr uint8_t RELAY_PIN = 26;

MFRC522 rfid(RFID_SS, RFID_RST);
HardwareSerial FingerSerial(2);
Adafruit_Fingerprint finger(&FingerSerial);

String serialLine;
bool enrollingCard = false;
bool enrollingFingerprint = false;
bool fingerprintReady = false;
uint16_t enrollmentSlot = 0;

String lastUID;
unsigned long lastCardAt = 0;
unsigned long lastFingerprintAt = 0;
unsigned long lastScanAt = 0;

void emitEvent(const char* eventName) {
  StaticJsonDocument<256> doc;
  doc["event"] = eventName;
  serializeJson(doc, Serial);
  Serial.println();
}

void emitDetail(const char* eventName, const char* detail) {
  StaticJsonDocument<256> doc;
  doc["event"] = eventName;
  doc["detail"] = detail;
  serializeJson(doc, Serial);
  Serial.println();
}

void emitUID(const char* eventName, const String& uid) {
  StaticJsonDocument<256> doc;
  doc["event"] = eventName;
  doc["uid"] = uid;
  serializeJson(doc, Serial);
  Serial.println();
}

void emitSlot(const char* eventName, int slot) {
  StaticJsonDocument<256> doc;
  doc["event"] = eventName;
  doc["slot"] = slot;
  serializeJson(doc, Serial);
  Serial.println();
}

String readUID() {
  String uid;

  for (byte i = 0; i < rfid.uid.size; i++) {
    char part[3];
    snprintf(part, sizeof(part), "%02X", rfid.uid.uidByte[i]);
    uid += part;
  }

  return uid;
}

bool enrollFingerprint(uint16_t slot) {
  int result;

  emitDetail("ENROLL_STATUS", "Coloque o dedo no sensor.");

  while (true) {
    result = finger.getImage();

    if (result == FINGERPRINT_OK) break;
    if (result != FINGERPRINT_NOFINGER) {
      emitDetail("ERROR", "Falha na primeira leitura biometrica.");
      return false;
    }

    delay(50);
  }

  result = finger.image2Tz(1);

  if (result != FINGERPRINT_OK) {
    emitDetail("ERROR", "Falha ao processar a primeira leitura.");
    return false;
  }

  emitDetail("ENROLL_STATUS", "Retire o dedo.");

  while (finger.getImage() != FINGERPRINT_NOFINGER) {
    delay(50);
  }

  emitDetail("ENROLL_STATUS", "Coloque o mesmo dedo novamente.");

  while (true) {
    result = finger.getImage();

    if (result == FINGERPRINT_OK) break;
    if (result != FINGERPRINT_NOFINGER) {
      emitDetail("ERROR", "Falha na segunda leitura biometrica.");
      return false;
    }

    delay(50);
  }

  result = finger.image2Tz(2);

  if (result != FINGERPRINT_OK) {
    emitDetail("ERROR", "Falha ao processar a segunda leitura.");
    return false;
  }

  result = finger.createModel();

  if (result != FINGERPRINT_OK) {
    emitDetail("ERROR", "As leituras nao corresponderam.");
    return false;
  }

  result = finger.storeModel(slot);

  if (result != FINGERPRINT_OK) {
    emitDetail("ERROR", "Falha ao salvar digital no sensor.");
    return false;
  }

  emitSlot("FP_ENROLLED", slot);
  return true;
}

void handleCommand(JsonDocument& doc) {
  const char* command = doc["cmd"] | "";

  if (strcmp(command, "PING") == 0) {
    emitEvent("READY");
    return;
  }

  if (strcmp(command, "ENROLL_CARD") == 0) {
    enrollingCard = true;
    emitDetail("ENROLL_STATUS", "Aproxime o cartao para cadastro.");
    return;
  }

  if (strcmp(command, "ENROLL_FP") == 0) {
    if (!fingerprintReady) {
      emitDetail("ERROR", "Sensor biometrico indisponivel.");
      return;
    }

    int slot = doc["slot"] | 0;

    if (slot < 1 || slot > 127) {
      emitDetail("ERROR", "Slot deve estar entre 1 e 127.");
      return;
    }

    enrollingFingerprint = true;
    enrollmentSlot = slot;
    enrollFingerprint(enrollmentSlot);
    enrollingFingerprint = false;
    return;
  }

  if (strcmp(command, "ACCESS_DECISION") == 0) {
    bool allow = doc["allow"] | false;

    if (ENABLE_RELAY && allow) {
      digitalWrite(RELAY_PIN, HIGH);
      delay(500);
      digitalWrite(RELAY_PIN, LOW);
    }

    emitDetail(
      "ENROLL_STATUS",
      allow ? "Computador autorizou." : "Computador negou."
    );
    return;
  }

  emitDetail("ERROR", "Comando desconhecido.");
}

void processSerial() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());

    if (c == '\r') continue;

    if (c == '\n') {
      if (serialLine.length() == 0) continue;

      if (serialLine.length() > 1024) {
        emitDetail("ERROR", "Comando serial muito grande.");
        serialLine = "";
        continue;
      }

      StaticJsonDocument<512> doc;
      DeserializationError error = deserializeJson(doc, serialLine);
      serialLine = "";

      if (error || !doc.is<JsonObject>()) {
        emitDetail("ERROR", "JSON serial invalido.");
        continue;
      }

      handleCommand(doc);
    } else {
      if (serialLine.length() < 1025) {
        serialLine += c;
      }
    }
  }
}

void scanCard() {
  if (millis() - lastScanAt < 80) return;

  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial()) return;

  lastScanAt = millis();

  String uid = readUID();

  if (uid != lastUID || millis() - lastCardAt > 2000) {
    lastUID = uid;
    lastCardAt = millis();

    emitUID(enrollingCard ? "CARD_ENROLLED" : "CARD", uid);

    if (enrollingCard) {
      enrollingCard = false;
    }
  }

  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();
}

void scanFingerprint() {
  if (!fingerprintReady || enrollingFingerprint) return;
  if (millis() - lastFingerprintAt < 300) return;

  int result = finger.getImage();

  if (result == FINGERPRINT_NOFINGER) return;

  lastFingerprintAt = millis();

  if (result != FINGERPRINT_OK) return;

  result = finger.image2Tz();

  if (result != FINGERPRINT_OK) {
    emitEvent("FP_NO_MATCH");
    return;
  }

  result = finger.fingerSearch();

  if (result == FINGERPRINT_OK) {
    emitSlot("FP_MATCH", finger.fingerID);
  } else {
    emitEvent("FP_NO_MATCH");
  }

  delay(350);
}

void setup() {
  Serial.begin(115200);

  pinMode(RELAY_PIN, OUTPUT);
  digitalWrite(RELAY_PIN, LOW);

  SPI.begin(18, 19, 23, RFID_SS);
  rfid.PCD_Init();

  FingerSerial.begin(57600, SERIAL_8N1, FP_RX, FP_TX);
  finger.begin(57600);

  fingerprintReady = finger.verifyPassword();

  if (fingerprintReady) {
    emitDetail("ENROLL_STATUS", "Sensor biometrico conectado.");
  } else {
    emitDetail("ERROR", "Sensor biometrico nao respondeu.");
  }

  emitEvent("READY");
}

void loop() {
  processSerial();
  scanCard();
  scanFingerprint();
}
