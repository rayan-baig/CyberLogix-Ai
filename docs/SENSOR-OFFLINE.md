# A sensor is offline: what to check

When a sensor stops reporting, CyberLogix cannot tell you if that fridge
or freezer gets too warm. This page says what offline means, what we do
about it, and what to check, in the order most likely to find the
problem.

The same steps are on the help page in the app (`/help#sensor-offline`),
and in the sensor's details in the console when it is offline.

## What offline means

A sensor shows as offline when we have heard nothing from it for 30
minutes. We text everyone on call once when that happens, because a
sensor that cannot report cannot warn you. As soon as it sends a reading
again, it shows as online.

## What to check

### 1. Check it has power

Look at the sensor itself. Is it plugged in, or are its batteries in and
charged? The sensor's card shows its battery level when it sends one; at
20% or less it says to replace it. Power cuts take sensors and fridges
down together, so check the fridge too.

### 2. Check the gateway or Wi-Fi

Most sensors talk through a small hub (a gateway) or your Wi-Fi. If
several sensors at one place went quiet at the same time, it is almost
always this. Make sure the gateway and the router have power and their
lights are on. Turn them off and on again if they are not.

### 3. Check the signal

The card shows signal strength when the sensor sends it. A walk-in's
metal walls block radio. Move the gateway closer, or out of a metal
cupboard, and keep the walk-in door shut while you test.

### 4. Did you make a new key for it?

Pressing New key on a sensor's card stops the old key working straight
away. The sensor needs the new key put into its settings before it can
report again.

### 5. Sensors from another maker

If the sensor sends through its maker's app or cloud, open that app and
check the sensor shows there. In the console, the card Fetch from a
sensor cloud says if we could not reach the maker's service, and why.

### 6. Check the account is paid up

When a trial or a paid term ends, the sensors are still watched for a
grace period. After that, readings are turned away until the account
moves to a paid plan, and every sensor shows as offline. A banner at the
top of the console says when this is the case.

### 7. Still offline?

Until it reports again, nobody is watching what is inside that fridge.
Check it by hand with a thermometer and write the reading down. Then
get in touch and give the sensor's name: the help page says how.

## Where you see it

In the console, an offline sensor's card says **offline** and has a
link, *Offline: what to check*. Open the sensor's details (press its
name or **History**) and the steps are there too. In the simple view
(`/today`) it is listed as gone quiet, with a link to these steps.
