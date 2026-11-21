
#include <ESP32Servo.h>
#include <Stepper.h>
#include <math.h>
#include <stdio.h>

// ===============================
// SERVO CONFIGURATION
// ===============================

#define SERVO_PIN 25

Servo myServo;

int currentServoAngle = 90;

// ===============================
// STEPPER CONFIGURATION
// ===============================

#define STEPS_PER_REV 2048

#define IN1 18
#define IN2 19
#define IN3 21
#define IN4 22

// IMPORTANT: Pin sequence is IN1, IN3, IN2, IN4
Stepper myStepper(STEPS_PER_REV, IN1, IN3, IN2, IN4);

int stepperSpeed = 10;

// ===============================
// TURN OFF STEPPER COILS
// ===============================

void releaseStepper() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

// ===============================
// DISPLAY HELP
// ===============================

void showHelp() {
  Serial.println("\n========== MOTOR CONTROL ==========");
  Serial.println("S 90    -> Servo to 90 degrees");
  Serial.println("S 0     -> Servo to 0 degrees");
  Serial.println("S 180   -> Servo to 180 degrees");
  Serial.println("M 90    -> Stepper clockwise 90 degrees");
  Serial.println("M -90   -> Stepper anticlockwise 90 degrees");
  Serial.println("M 360   -> Stepper one full revolution");
  Serial.println("V 10    -> Set stepper speed to 10 RPM");
  Serial.println("H       -> Show help");
  Serial.println("===================================");
}

// ===============================
// SETUP
// ===============================

void setup() {

  Serial.begin(115200);

  // Servo initialization
  myServo.setPeriodHertz(50);
  myServo.attach(SERVO_PIN, 500, 2400);

  myServo.write(currentServoAngle);

  // Stepper initialization
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  myStepper.setSpeed(stepperSpeed);

  releaseStepper();

  Serial.println("\nSMART WASTE SEGREGATION");
  Serial.println("SERVO + STEPPER TEST");

  showHelp();
}

// ===============================
// MAIN LOOP
// ===============================

void loop() {

  if (Serial.available()) {

    String input = Serial.readStringUntil('\n');
    input.trim();

    if (input.length() == 0) {
      return;
    }

    if (input.equalsIgnoreCase("H")) {
      showHelp();
      return;
    }

    char command;
    float value;
    char extra;

    // Validate command format
    int count = sscanf(input.c_str(), " %c %f %c",
                       &command, &value, &extra);

    if (count != 2) {
      Serial.println("Invalid command. Enter H for help.");
      return;
    }

    command = toupper(command);

    // ===========================
    // SERVO CONTROL
    // ===========================

    if (command == 'S') {

      if (value < 0 || value > 180) {
        Serial.println("Servo angle must be 0 to 180.");
        return;
      }

      int angle = (int)round(value);

      myServo.write(angle);

      currentServoAngle = angle;

      Serial.print("Servo moved to ");
      Serial.print(angle);
      Serial.println(" degrees.");
    }

    // ===========================
    // STEPPER CONTROL
    // ===========================

    else if (command == 'M') {

      if (value < -360 || value > 360) {
        Serial.println("Stepper angle must be between -360 and 360.");
        return;
      }

      long steps = lround(value * STEPS_PER_REV / 360.0);

      Serial.print("Stepper rotating ");
      Serial.print(value);
      Serial.println(" degrees...");

      myStepper.step(steps);

      releaseStepper();

      Serial.println("Stepper movement completed.");
    }

    // ===========================
    // STEPPER SPEED CONTROL
    // ===========================

    else if (command == 'V') {

      if (value < 1 || value > 15) {
        Serial.println("Speed must be between 1 and 15 RPM.");
        return;
      }

      stepperSpeed = (int)round(value);

      myStepper.setSpeed(stepperSpeed);

      Serial.print("Stepper speed set to ");
      Serial.print(stepperSpeed);
      Serial.println(" RPM.");
    }

    else {
      Serial.println("Unknown command. Enter H for help.");
    }
  }
}
