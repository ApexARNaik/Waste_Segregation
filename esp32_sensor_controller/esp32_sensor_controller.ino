/*
 * Smart Waste Segregation - ESP32 DevKit V1 Sensor & Actuator Controller
 * 
 * Pin Configuration:
 *   - Raindrop Sensor Analog (AO):       GPIO34 (ADC1_CH6, 12-bit: 0-4095)
 *   - Raindrop Sensor Digital (DO):      GPIO32 (Digital input)
 *   - IR Obstacle Sensor (OUT):          GPIO27 (Digital input, active-LOW)
 *   - Inductive Proximity Sensor (OUT):  GPIO26 (NPN open-collector with external 10k pull-up to 3.3V, active-LOW)
 *   - Servo Signal:                      GPIO25 (PWM control via ESP32Servo)
 *   - ULN2003 Stepper Driver:            IN1 -> GPIO18, IN2 -> GPIO19, IN3 -> GPIO21, IN4 -> GPIO22
 * 
 * Stepper Coil Sequence:
 *   Preserves verified working sequence: IN1 (18), IN3 (21), IN2 (19), IN4 (22)
 * 
 * Non-blocking motor movement ensures continuous sensor sampling and telemetry streaming.
 * Serial Protocol: 115200 baud, newline-delimited JSON Lines.
 */

#include <Arduino.h>
#include <ESP32Servo.h>

// ==========================================
// PIN DEFINITIONS
// ==========================================
#define RAIN_ANALOG_PIN    34
#define RAIN_DIGITAL_PIN   32
#define IR_PIN             27
#define INDUCTIVE_PIN      26
#define SERVO_PIN          25

#define IN1_PIN            18
#define IN2_PIN            19
#define IN3_PIN            21
#define IN4_PIN            22

// ==========================================
// SERVO CONFIGURATION
// ==========================================
Servo myServo;
int currentServoAngle = 90;

// ==========================================
// STEPPER CONFIGURATION (28BYJ-48 + ULN2003)
// ==========================================
#define STEPS_PER_REV 2048

int stepperSpeedRpm = 5; // Default 5 RPM (range 1-10)
unsigned long stepIntervalUs = 0;
unsigned long lastStepTimeUs = 0;

volatile bool stepperBusy = false;
volatile long stepperStepsRemaining = 0;
volatile int stepperStepDirection = 1; // +1 clockwise, -1 anticlockwise
long stepperAngleEstimated = 0; // Commanded accumulated degrees since boot (estimated, NOT encoder)
int currentStepIndex = 0;

// 4-step canonical drive sequence for ULN2003 (coils 1+2, 2+3, 3+4, 4+1)
// Mapping: IN1=18, IN2=19, IN3=21, IN4=22
const uint8_t stepMatrix[4][4] = {
  {HIGH, HIGH, LOW,  LOW},   // IN1, IN2
  {LOW,  HIGH, HIGH, LOW},   // IN2, IN3
  {LOW,  LOW,  HIGH, HIGH},  // IN3, IN4
  {HIGH, LOW,  LOW,  HIGH}   // IN4, IN1
};

void releaseStepper() {
  digitalWrite(IN1_PIN, LOW);
  digitalWrite(IN2_PIN, LOW);
  digitalWrite(IN3_PIN, LOW);
  digitalWrite(IN4_PIN, LOW);
}

void setStepperSpeed(int rpm) {
  if (rpm < 1) rpm = 1;
  if (rpm > 10) rpm = 10;
  stepperSpeedRpm = rpm;
  // Microseconds per step: 60,000,000 / (2048 * rpm)
  stepIntervalUs = 60000000UL / ((unsigned long)STEPS_PER_REV * stepperSpeedRpm);
}

void doOneStep(int dir) {
  currentStepIndex = (currentStepIndex + dir + 4) % 4;
  digitalWrite(IN1_PIN, stepMatrix[currentStepIndex][0]);
  digitalWrite(IN2_PIN, stepMatrix[currentStepIndex][1]);
  digitalWrite(IN3_PIN, stepMatrix[currentStepIndex][2]);
  digitalWrite(IN4_PIN, stepMatrix[currentStepIndex][3]);
}

// ==========================================
// SENSOR READINGS & TIMERS
// ==========================================
int rainAnalogRaw = 0;
int rainDigitalRaw = 1;
int irRaw = 1;
bool irDetected = false;
int inductiveRaw = 1;
bool metalDetected = false;

unsigned long lastSensorSampleTime = 0;
const unsigned long SENSOR_SAMPLE_INTERVAL_MS = 150; // 150 ms sample rate

unsigned long lastTelemetryTime = 0;
const unsigned long TELEMETRY_INTERVAL_MS = 500;     // 500 ms JSON Lines stream

// Serial input buffer
String inputBuffer = "";

// ==========================================
// SENSOR READING FUNCTION
// ==========================================
void sampleSensors() {
  // Raindrop sensor: raw analog ADC (0-4095) and digital state
  rainAnalogRaw = analogRead(RAIN_ANALOG_PIN);
  rainDigitalRaw = digitalRead(RAIN_DIGITAL_PIN);

  // IR sensor: active-LOW detection
  irRaw = digitalRead(IR_PIN);
  irDetected = (irRaw == LOW);

  // Inductive metal sensor: active-LOW detection (external 10k pull-up to 3.3V)
  inductiveRaw = digitalRead(INDUCTIVE_PIN);
  metalDetected = (inductiveRaw == LOW);
}

// ==========================================
// SEND JSON TELEMETRY
// ==========================================
void sendTelemetry() {
  char jsonBuffer[320];
  snprintf(
    jsonBuffer,
    sizeof(jsonBuffer),
    "{\"type\":\"telemetry\",\"uptime_ms\":%lu,\"rain_analog\":%d,\"rain_digital\":%d,"
    "\"ir_raw\":%d,\"ir_detected\":%s,\"inductive_raw\":%d,\"metal_detected\":%s,"
    "\"servo_angle\":%d,\"stepper_angle_estimated\":%ld,\"stepper_speed_rpm\":%d,\"stepper_busy\":%s}\n",
    millis(),
    rainAnalogRaw,
    rainDigitalRaw,
    irRaw,
    irDetected ? "true" : "false",
    inductiveRaw,
    metalDetected ? "true" : "false",
    currentServoAngle,
    stepperAngleEstimated,
    stepperSpeedRpm,
    stepperBusy ? "true" : "false"
  );
  Serial.print(jsonBuffer);
}

// ==========================================
// SEND JSON ACKNOWLEDGEMENT
// ==========================================
void sendAck(const char* command, const char* valueStr, bool success, const char* message) {
  char ackBuffer[256];
  snprintf(
    ackBuffer,
    sizeof(ackBuffer),
    "{\"type\":\"ack\",\"command\":\"%s\",\"value\":%s,\"success\":%s,\"message\":\"%s\"}\n",
    command,
    valueStr,
    success ? "true" : "false",
    message
  );
  Serial.print(ackBuffer);
}

// ==========================================
// PROCESS COMMANDS
// ==========================================
void processCommand(String cmdLine) {
  cmdLine.trim();
  if (cmdLine.length() == 0) return;

  if (cmdLine.equalsIgnoreCase("PING")) {
    sendAck("PING", "null", true, "PONG - Sensor controller ready");
    return;
  }

  int firstSpace = cmdLine.indexOf(' ');
  String cmd = (firstSpace == -1) ? cmdLine : cmdLine.substring(0, firstSpace);
  cmd.toUpperCase();
  String argStr = (firstSpace == -1) ? "" : cmdLine.substring(firstSpace + 1);
  argStr.trim();

  // SERVO <angle>
  if (cmd.equals("SERVO")) {
    if (argStr.length() == 0) {
      sendAck("SERVO", "null", false, "Missing servo angle parameter");
      return;
    }
    int angle = argStr.toInt();
    if (angle < 0 || angle > 180) {
      char valBuf[16];
      snprintf(valBuf, sizeof(valBuf), "%d", angle);
      sendAck("SERVO", valBuf, false, "Servo angle must be between 0 and 180");
      return;
    }
    myServo.write(angle);
    currentServoAngle = angle;
    char valBuf[16];
    char msgBuf[64];
    snprintf(valBuf, sizeof(valBuf), "%d", angle);
    snprintf(msgBuf, sizeof(msgBuf), "Servo set to %d deg", angle);
    sendAck("SERVO", valBuf, true, msgBuf);
    return;
  }

  // STEPPER <angle>
  if (cmd.equals("STEPPER")) {
    if (argStr.length() == 0) {
      sendAck("STEPPER", "null", false, "Missing stepper angle parameter");
      return;
    }
    long angle = argStr.toInt();
    if (angle < -360 || angle > 360) {
      char valBuf[16];
      snprintf(valBuf, sizeof(valBuf), "%ld", angle);
      sendAck("STEPPER", valBuf, false, "Stepper angle must be between -360 and 360");
      return;
    }
    if (stepperBusy) {
      char valBuf[16];
      snprintf(valBuf, sizeof(valBuf), "%ld", angle);
      sendAck("STEPPER", valBuf, false, "Stepper is currently busy");
      return;
    }

    if (angle == 0) {
      sendAck("STEPPER", "0", true, "Stepper rotation 0 deg complete");
      return;
    }

    long steps = lround((float)angle * STEPS_PER_REV / 360.0);
    stepperStepDirection = (steps >= 0) ? 1 : -1;
    stepperStepsRemaining = labs(steps);
    stepperAngleEstimated += angle;
    lastStepTimeUs = micros();
    stepperBusy = true;

    char valBuf[16];
    char msgBuf[64];
    snprintf(valBuf, sizeof(valBuf), "%ld", angle);
    snprintf(msgBuf, sizeof(msgBuf), "Stepper rotating %ld deg (%ld steps)", angle, steps);
    sendAck("STEPPER", valBuf, true, msgBuf);
    return;
  }

  // SPEED <rpm>
  if (cmd.equals("SPEED")) {
    if (argStr.length() == 0) {
      sendAck("SPEED", "null", false, "Missing speed RPM parameter");
      return;
    }
    int rpm = argStr.toInt();
    if (rpm < 1 || rpm > 10) {
      char valBuf[16];
      snprintf(valBuf, sizeof(valBuf), "%d", rpm);
      sendAck("SPEED", valBuf, false, "Speed must be between 1 and 10 RPM");
      return;
    }
    setStepperSpeed(rpm);
    char valBuf[16];
    char msgBuf[64];
    snprintf(valBuf, sizeof(valBuf), "%d", rpm);
    snprintf(msgBuf, sizeof(msgBuf), "Stepper speed set to %d RPM", rpm);
    sendAck("SPEED", valBuf, true, msgBuf);
    return;
  }

  // Unknown command
  sendAck(cmd.c_str(), "null", false, "Unknown command");
}

// ==========================================
// SETUP
// ==========================================
void setup() {
  Serial.begin(115200);

  // Configure ADC resolution (12-bit: 0-4095)
  analogReadResolution(12);

  // Configure sensor pins
  pinMode(RAIN_DIGITAL_PIN, INPUT);
  pinMode(IR_PIN, INPUT);
  // Inductive sensor uses external 10k pull-up to 3.3V. Do not enable internal pull-up.
  pinMode(INDUCTIVE_PIN, INPUT);

  // Configure stepper motor driver pins
  pinMode(IN1_PIN, OUTPUT);
  pinMode(IN2_PIN, OUTPUT);
  pinMode(IN3_PIN, OUTPUT);
  pinMode(IN4_PIN, OUTPUT);
  releaseStepper();
  setStepperSpeed(stepperSpeedRpm);

  // Configure Servo
  myServo.setPeriodHertz(50);
  myServo.attach(SERVO_PIN, 500, 2400);
  myServo.write(currentServoAngle);

  // Initial sensor sample
  sampleSensors();
}

// ==========================================
// MAIN NON-BLOCKING LOOP
// ==========================================
void loop() {
  unsigned long now = millis();

  // 1. Non-blocking stepper stepping
  if (stepperBusy) {
    unsigned long nowUs = micros();
    if (nowUs - lastStepTimeUs >= stepIntervalUs) {
      lastStepTimeUs = nowUs;
      doOneStep(stepperStepDirection);
      stepperStepsRemaining--;
      if (stepperStepsRemaining <= 0) {
        stepperBusy = false;
        releaseStepper();
      }
    }
  }

  // 2. Periodic sensor sampling (~150 ms)
  if (now - lastSensorSampleTime >= SENSOR_SAMPLE_INTERVAL_MS) {
    lastSensorSampleTime = now;
    sampleSensors();
  }

  // 3. Periodic JSON Lines telemetry output (~500 ms)
  if (now - lastTelemetryTime >= TELEMETRY_INTERVAL_MS) {
    lastTelemetryTime = now;
    sendTelemetry();
  }

  // 4. Non-blocking serial command reading
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        processCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      if (inputBuffer.length() < 128) {
        inputBuffer += c;
      }
    }
  }
}
