"""
IoT Sensor Views
-----------------
1. Farmer Field Dashboard  — multi-field overview with live sensor data
2. Field Detail            — time-series readings + alerts for one field
3. Sensor Ingest API       — REST endpoint for field nodes to POST readings
4. Irrigation Control      — manual valve override
5. Alert Dashboard         — all unread alerts
6. Metrics Dashboard       — evaluation metrics (PDR, latency, accuracy...)
7. Zone Map                — K-medoids zone visualisation
"""

import json
import logging
from datetime import timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    Field, SensorNode, SensorReading, IrrigationCommand,
    SensorThreshold, SensorAlert, CropPredictionLog, SystemMetricsLog, FieldZone
)
from .services.alert_engine import check_reading
from .services.xgboost_predictor import predict_from_sensor, predict_crop

logger = logging.getLogger(__name__)


# ── helpers ───────────────────────────────────────────────────────────────────

def _require_verified(request):
    """Return AgroProfile or None; flash error if not verified."""
    try:
        profile = request.user.agro_profile
        if not profile.is_verified:
            messages.warning(request, 'Your account is pending verification by admin.')
        return profile
    except Exception:
        return None


# ── 1. FIELD DASHBOARD ───────────────────────────────────────────────────────

@login_required(login_url='/agrolease/login/')
def field_dashboard(request):
    """Multi-field overview — all fields owned by the logged-in user."""
    fields = Field.objects.filter(owner=request.user, is_active=True).prefetch_related(
        'nodes', 'alerts', 'sensor_readings'
    )

    # Attach latest reading to each field
    field_data = []
    for field in fields:
        latest = field.sensor_readings.first()
        unread_alerts = field.alerts.filter(is_read=False).count()
        field_data.append({
            'field': field,
            'latest': latest,
            'unread_alerts': unread_alerts,
            'node_count': field.nodes.filter(is_active=True).count(),
        })

    context = {
        'field_data': field_data,
        'total_fields': fields.count(),
        'total_alerts': SensorAlert.objects.filter(field__owner=request.user, is_read=False).count(),
    }
    return render(request, 'iot_sensor/field_dashboard.html', context)


# ── 2. FIELD DETAIL ──────────────────────────────────────────────────────────

@login_required(login_url='/agrolease/login/')
def field_detail(request, field_id):
    field = get_object_or_404(Field, pk=field_id, owner=request.user)
    readings = field.sensor_readings.all()[:96]   # last 96 readings (~8 hrs @ 5-min interval)
    alerts   = field.alerts.all()[:20]
    nodes    = field.nodes.filter(is_active=True)
    zones    = field.zones.all()
    thresholds = field.thresholds.all()
    last_cmd = field.irrigation_commands.first()

    # Chart data for JS (last 24 readings)
    chart_labels = []
    chart_moisture = []
    chart_temp = []
    for r in reversed(list(readings[:24])):
        chart_labels.append(r.recorded_at.strftime('%H:%M'))
        chart_moisture.append(r.soil_moisture_pct)
        chart_temp.append(r.temperature_c)

    context = {
        'field': field,
        'readings': readings,
        'alerts': alerts,
        'nodes': nodes,
        'zones': zones,
        'thresholds': thresholds,
        'last_irrigation': last_cmd,
        'chart_labels': json.dumps(chart_labels),
        'chart_moisture': json.dumps(chart_moisture),
        'chart_temp': json.dumps(chart_temp),
        'latest': readings.first() if readings else None,
    }
    return render(request, 'iot_sensor/field_detail.html', context)


# ── 3. SENSOR INGEST API ─────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def sensor_ingest(request):
    """
    REST endpoint for ESP32 / Raspberry Pi to POST sensor data.
    Expected JSON body:
    {
        "device_id": "ESP32-001",
        "soil_moisture_pct": 32.4,
        "nitrogen_ppm": 140,
        "phosphorus_ppm": 58,
        "potassium_ppm": 201,
        "soil_ph": 6.8,
        "temperature_c": 28.3,
        "humidity_pct": 72.1,
        "wind_speed_kmh": 8.5,
        "solar_lux": 45000,
        "rainfall_mm": 0,
        "water_level_pct": 85,
        "packet_id": "PKT-20240115-001",
        "rssi_dbm": -72
    }
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    device_id = data.get('device_id', '').strip()
    if not device_id:
        return JsonResponse({'error': 'device_id required'}, status=400)

    try:
        node = SensorNode.objects.select_related('field').get(device_id=device_id, is_active=True)
    except SensorNode.DoesNotExist:
        return JsonResponse({'error': 'Unknown device_id'}, status=403)

    # Build reading
    reading = SensorReading.objects.create(
        node=node,
        field=node.field,
        soil_moisture_pct  = data.get('soil_moisture_pct'),
        nitrogen_ppm       = data.get('nitrogen_ppm'),
        phosphorus_ppm     = data.get('phosphorus_ppm'),
        potassium_ppm      = data.get('potassium_ppm'),
        soil_ph            = data.get('soil_ph'),
        temperature_c      = data.get('temperature_c'),
        humidity_pct       = data.get('humidity_pct'),
        wind_speed_kmh     = data.get('wind_speed_kmh'),
        solar_lux          = data.get('solar_lux'),
        rainfall_mm        = data.get('rainfall_mm'),
        water_level_pct    = data.get('water_level_pct'),
        packet_id          = data.get('packet_id', ''),
        rssi_dbm           = data.get('rssi_dbm'),
        is_valid           = True,
    )

    # Update node heartbeat
    node.mark_seen()
    if 'battery_pct' in data:
        node.battery_pct = data['battery_pct']
        node.save(update_fields=['battery_pct'])

    # Run alert engine
    alerts = check_reading(reading)

    # Run crop prediction if NPK data present
    crop_result = None
    if all(v is not None for v in [reading.nitrogen_ppm, reading.phosphorus_ppm,
                                    reading.potassium_ppm, reading.temperature_c]):
        crop_result = predict_from_sensor(reading)
        CropPredictionLog.objects.create(
            field=node.field,
            reading=reading,
            nitrogen   = reading.nitrogen_ppm or 0,
            phosphorus = reading.phosphorus_ppm or 0,
            potassium  = reading.potassium_ppm or 0,
            temperature= reading.temperature_c or 25,
            humidity   = reading.humidity_pct or 60,
            ph         = reading.soil_ph or 6.5,
            rainfall   = reading.rainfall_mm or 0,
            recommended_crop = crop_result['crop'],
            confidence_pct   = crop_result['confidence'],
            top3_crops       = crop_result.get('top3', []),
            model_version    = crop_result.get('model', 'unknown'),
        )

    # Check for pending irrigation commands to return
    pending_cmd = IrrigationCommand.objects.filter(
        node=node, status='pending'
    ).first()

    response = {
        'reading_id': reading.pk,
        'alerts_triggered': len(alerts),
        'crop_recommendation': crop_result['crop'] if crop_result else None,
    }
    if pending_cmd:
        response['command'] = {
            'id': pending_cmd.pk,
            'valve_open': pending_cmd.valve_open,
            'duration_minutes': pending_cmd.duration_minutes,
        }
        pending_cmd.status = 'sent'
        pending_cmd.save(update_fields=['status'])

    return JsonResponse(response, status=201)


# ── 4. IRRIGATION CONTROL ────────────────────────────────────────────────────

@login_required(login_url='/agrolease/login/')
@require_POST
def irrigation_control(request, field_id):
    """Manual valve override from farmer dashboard."""
    field = get_object_or_404(Field, pk=field_id, owner=request.user)
    node_id = request.POST.get('node_id')
    action  = request.POST.get('action')   # 'open' or 'close'
    duration = int(request.POST.get('duration_minutes', 30))

    node = get_object_or_404(SensorNode, pk=node_id, field=field)

    IrrigationCommand.objects.create(
        node=node,
        field=field,
        issued_by=request.user,
        source='manual',
        valve_open=(action == 'open'),
        duration_minutes=duration,
    )
    messages.success(request, f"Irrigation command ({action}) sent to {node.device_id}.")
    return redirect('iot_sensor:field_detail', field_id=field_id)


# ── 5. ALERT DASHBOARD ───────────────────────────────────────────────────────

@login_required(login_url='/agrolease/login/')
def alert_dashboard(request):
    alerts = SensorAlert.objects.filter(
        field__owner=request.user
    ).select_related('field', 'reading').order_by('-alert_created')[:50]

    if request.method == 'POST':
        alert_id = request.POST.get('alert_id')
        if alert_id:
            SensorAlert.objects.filter(pk=alert_id, field__owner=request.user).update(
                is_read=True, is_resolved=True, resolved_at=timezone.now()
            )
        return redirect('iot_sensor:alert_dashboard')

    context = {'alerts': alerts}
    return render(request, 'iot_sensor/alert_dashboard.html', context)


# ── 6. METRICS DASHBOARD ─────────────────────────────────────────────────────

@login_required(login_url='/agrolease/login/')
def metrics_dashboard(request):
    """Shows all evaluation metrics from the proposal."""
    logs = SystemMetricsLog.objects.all()[:30]
    latest = logs.first()

    # Compute today's alert latency from DB
    today = timezone.now().date()
    today_alerts = SensorAlert.objects.filter(
        field__owner=request.user,
        alert_created__date=today
    )
    total_alerts = today_alerts.count()
    under_2min = today_alerts.filter(
        alert_created__gte=timezone.now() - timedelta(minutes=2)
    ).count()

    # Compute crop prediction accuracy from farmer feedback
    confirmed = CropPredictionLog.objects.filter(
        field__owner=request.user,
        farmer_agreed__isnull=False
    )
    accuracy = None
    if confirmed.exists():
        accuracy = round(confirmed.filter(farmer_agreed=True).count() / confirmed.count() * 100, 1)

    # Node uptime: nodes seen in last 15 minutes
    threshold_time = timezone.now() - timedelta(minutes=15)
    active_nodes = SensorNode.objects.filter(field__owner=request.user, is_active=True)
    online_nodes = active_nodes.filter(last_seen__gte=threshold_time).count()
    total_nodes  = active_nodes.count()

    context = {
        'logs': logs,
        'latest': latest,
        'today_alerts': total_alerts,
        'alerts_under_2min': under_2min,
        'crop_accuracy': accuracy,
        'online_nodes': online_nodes,
        'total_nodes': total_nodes,
    }
    return render(request, 'iot_sensor/metrics_dashboard.html', context)


# ── 7. ZONE MAP (K-medoids result visualisation) ─────────────────────────────

@login_required(login_url='/agrolease/login/')
def zone_map(request, field_id):
    field = get_object_or_404(Field, pk=field_id, owner=request.user)
    zones = field.zones.all()
    nodes = field.nodes.filter(is_active=True).exclude(latitude=None)

    if request.method == 'POST':
        k = int(request.POST.get('k', 3))
        from .services.zone_optimizer import optimise_field_zones
        created = optimise_field_zones(field, k)
        messages.success(request, f"{len(created)} zones computed using K-medoids + Weiszfeld algorithm.")
        return redirect('iot_sensor:zone_map', field_id=field_id)

    context = {
        'field': field,
        'zones': zones,
        'nodes': nodes,
        'nodes_json': json.dumps([
            {'id': n.pk, 'lat': float(n.latitude), 'lng': float(n.longitude),
             'label': n.device_id, 'zone': n.placement_zone}
            for n in nodes
        ]),
        'zones_json': json.dumps([
            {'label': z.zone_label,
             'centroid_lat': float(z.centroid_lat) if z.centroid_lat else None,
             'centroid_lng': float(z.centroid_lng) if z.centroid_lng else None,
             'valve_lat': float(z.valve_lat) if z.valve_lat else None,
             'valve_lng': float(z.valve_lng) if z.valve_lng else None}
            for z in zones
        ]),
    }
    return render(request, 'iot_sensor/zone_map.html', context)


# ── 8. LIVE READINGS API (for JS polling) ────────────────────────────────────

@login_required(login_url='/agrolease/login/')
def readings_api(request, field_id):
    """Returns latest 24 readings as JSON for chart refresh."""
    field = get_object_or_404(Field, pk=field_id, owner=request.user)
    readings = list(field.sensor_readings.all()[:24])
    data = [
        {
            'time': r.recorded_at.strftime('%H:%M'),
            'soil_moisture': r.soil_moisture_pct,
            'temperature':   r.temperature_c,
            'humidity':      r.humidity_pct,
            'nitrogen':      r.nitrogen_ppm,
            'phosphorus':    r.phosphorus_ppm,
            'potassium':     r.potassium_ppm,
            'wind_speed':    r.wind_speed_kmh,
            'solar_lux':     r.solar_lux,
            'water_level':   r.water_level_pct,
        }
        for r in reversed(readings)
    ]
    return JsonResponse({'readings': data})
