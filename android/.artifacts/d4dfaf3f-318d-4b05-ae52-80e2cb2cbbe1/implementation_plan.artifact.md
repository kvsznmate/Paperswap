# UI Refinement Plan

The goal is to update the app's UI by changing the main background color and removing the card counter from the top-right corner.

## User Review Required

> [!IMPORTANT]
> I need to know the specific color or Hex code you'd like to use for the background. For now, I've prepared the code to be easily updated once you provide the color.

## Proposed Changes

### UI Components

#### [MODIFY] [MainActivity.kt](file:///C:/Users/matek_yulq090/Desktop/Antigrav_test/android/app/src/main/java/com/newsswipe/app/MainActivity.kt)
- Remove the `when (val state = uiState)` block in the top bar that renders the card counter.
- Update the `Surface` and `Column` background colors to the new color (once specified).

## Verification Plan

### Manual Verification
- Deploy the app to the device/emulator.
- Confirm the card counter is no longer visible in the top-right corner.
- Confirm the background color has changed as requested.
