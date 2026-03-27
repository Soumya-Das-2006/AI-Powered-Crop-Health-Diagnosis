from django.shortcuts import render, redirect
from django.contrib import messages
import json

# Mock data for demonstration
MOCK_LANDS = [
    {
        'id': 1,
        'location': 'Mysore, Karnataka',
        'size': '5 acres',
        'soil_type': 'Red Soil',
        'water_source': 'Well',
        'suitable_crops': 'Rice, Sugarcane',
        'rent': '₹25,000/season',
        'duration': '6 months',
        'images': ['/static/assets/img/land1.jpg'],
        'owner': 'Rajesh Kumar',
        'status': 'available'
    },
    {
        'id': 2,
        'location': 'Coimbatore, Tamil Nadu',
        'size': '3 acres',
        'soil_type': 'Black Soil',
        'water_source': 'Canal',
        'suitable_crops': 'Cotton, Maize',
        'rent': '₹18,000/year',
        'duration': '12 months',
        'images': ['/static/assets/img/land2.jpg'],
        'owner': 'Priya Sharma',
        'status': 'available'
    },
    {
        'id': 3,
        'location': 'Hyderabad, Telangana',
        'size': '8 acres',
        'soil_type': 'Alluvial Soil',
        'water_source': 'Borewell',
        'suitable_crops': 'Wheat, Vegetables',
        'rent': '₹40,000/season',
        'duration': '4 months',
        'images': ['/static/assets/img/land3.jpg'],
        'owner': 'Venkat Reddy',
        'status': 'available'
    }
]

MOCK_REQUESTS = [
    {
        'id': 1,
        'land_id': 1,
        'farmer_name': 'Amit Singh',
        'status': 'pending',
        'request_date': '2024-01-15',
        'message': 'I have experience in rice cultivation and can provide good yield.'
    },
    {
        'id': 2,
        'land_id': 2,
        'farmer_name': 'Sunita Patel',
        'status': 'approved',
        'request_date': '2024-01-10',
        'message': 'Looking for cotton farming opportunity.'
    }
]

def role_selection(request):
    """Role selection page"""
    if request.method == 'POST':
        role = request.POST.get('role')
        request.session['user_role'] = role
        if role == 'owner':
            return redirect('agrolease:owner_dashboard')
        elif role == 'farmer':
            return redirect('agrolease:farmer_dashboard')
        elif role == 'admin':
            return redirect('agrolease:admin_dashboard')

    return render(request, 'agrolease/role_selection.html')

def login_view(request):
    """Login page (UI only)"""
    if request.method == 'POST':
        phone = request.POST.get('phone')
        # Mock OTP sending
        messages.success(request, f'OTP sent to {phone}')
        request.session['phone'] = phone
        return redirect('agrolease:otp')

    return render(request, 'agrolease/login.html')

def otp_view(request):
    """OTP verification page (UI only)"""
    if request.method == 'POST':
        otp = request.POST.get('otp')
        # Mock OTP verification
        if otp == '123456':  # TODO: Replace with real OTP via SMS (Twilio/MSG91) before production
            messages.success(request, 'Login successful!')
            return redirect('agrolease:role_selection')
        else:
            messages.error(request, 'Invalid OTP. (Demo: use 123456)')

    return render(request, 'agrolease/otp.html')

def register_view(request):
    """Registration page (UI only)"""
    if request.method == 'POST':
        # Mock registration
        messages.success(request, 'Registration successful! Please login.')
        return redirect('agrolease:login')

    return render(request, 'agrolease/register.html')

def owner_dashboard(request):
    """Owner dashboard"""
    user_role = request.session.get('user_role', 'owner')
    if user_role != 'owner':
        return redirect('agrolease:role_selection')

    # Mock owner's lands
    owner_lands = [land for land in MOCK_LANDS if land['status'] == 'available'][:2]
    # Mock requests for owner's lands
    owner_requests = MOCK_REQUESTS

    context = {
        'lands': owner_lands,
        'requests': owner_requests,
        'total_lands': len(owner_lands),
        'pending_requests': len([r for r in owner_requests if r['status'] == 'pending'])
    }
    return render(request, 'agrolease/owner_dashboard.html', context)

def add_land(request):
    """Add land form"""
    if request.method == 'POST':
        # Mock land addition
        messages.success(request, 'Land added successfully!')
        return redirect('agrolease:owner_dashboard')

    return render(request, 'agrolease/add_land.html')

def owner_requests(request):
    """Owner's lease requests"""
    requests = MOCK_REQUESTS
    context = {'requests': requests}
    return render(request, 'agrolease/owner_requests.html', context)

def farmer_dashboard(request):
    """Farmer dashboard"""
    user_role = request.session.get('user_role', 'farmer')
    if user_role != 'farmer':
        return redirect('agrolease:role_selection')

    # Mock farmer's requests
    farmer_requests = MOCK_REQUESTS[:1]  # One request
    active_leases = [r for r in MOCK_REQUESTS if r['status'] == 'approved']

    context = {
        'requests': farmer_requests,
        'active_leases': active_leases,
        'total_requests': len(farmer_requests),
        'active_count': len(active_leases)
    }
    return render(request, 'agrolease/farmer_dashboard.html', context)

def search_land(request):
    """Search and filter lands"""
    lands = MOCK_LANDS

    # Mock filtering
    location = request.GET.get('location', '')
    soil_type = request.GET.get('soil_type', '')
    rent_range = request.GET.get('rent_range', '')

    if location:
        lands = [l for l in lands if location.lower() in l['location'].lower()]
    if soil_type:
        lands = [l for l in lands if soil_type.lower() in l['soil_type'].lower()]

    context = {
        'lands': lands,
        'filters': {
            'location': location,
            'soil_type': soil_type,
            'rent_range': rent_range
        }
    }
    return render(request, 'agrolease/search_land.html', context)

def request_lease(request, land_id):
    """Request lease for a land"""
    land = next((l for l in MOCK_LANDS if l['id'] == land_id), None)
    if not land:
        messages.error(request, 'Land not found')
        return redirect('agrolease:search_land')

    if request.method == 'POST':
        # Mock lease request
        messages.success(request, 'Lease request sent successfully!')
        return redirect('agrolease:farmer_dashboard')

    context = {'land': land}
    return render(request, 'agrolease/request_lease.html', context)

def admin_dashboard(request):
    """Admin dashboard"""
    user_role = request.session.get('user_role', 'admin')
    if user_role != 'admin':
        return redirect('agrolease:role_selection')

    # Mock admin stats
    context = {
        'pending_verifications': 5,
        'pending_approvals': 3,
        'total_users': 150,
        'active_leases': 25
    }
    return render(request, 'agrolease/admin_dashboard.html', context)

def admin_verifications(request):
    """User verification management"""
    # Mock pending verifications
    verifications = [
        {'id': 1, 'name': 'Rajesh Kumar', 'type': 'Identity', 'status': 'pending'},
        {'id': 2, 'name': 'Priya Sharma', 'type': 'Land Document', 'status': 'pending'},
    ]
    context = {'verifications': verifications}
    return render(request, 'agrolease/admin_verifications.html', context)

def admin_approvals(request):
    """Land approval management"""
    # Mock pending approvals
    approvals = [
        {'id': 1, 'land_location': 'Mysore, Karnataka', 'owner': 'Rajesh Kumar', 'status': 'pending'},
        {'id': 2, 'land_location': 'Coimbatore, Tamil Nadu', 'owner': 'Priya Sharma', 'status': 'pending'},
    ]
    context = {'approvals': approvals}
    return render(request, 'agrolease/admin_approvals.html', context)

def agreement_preview(request, request_id):
    """Agreement preview"""
    lease_request = next((r for r in MOCK_REQUESTS if r['id'] == request_id), None)
    land = next((l for l in MOCK_LANDS if l['id'] == lease_request['land_id']), None) if lease_request else None

    if not lease_request or not land:
        messages.error(request, 'Agreement not found')
        return redirect('agrolease:farmer_dashboard')

    context = {
        'request': lease_request,
        'land': land,
        'agreement_text': f"""
        LEASE AGREEMENT

        This agreement is made between {land['owner']} (Land Owner) and {lease_request['farmer_name']} (Farmer/Tenant).

        Land Details:
        - Location: {land['location']}
        - Size: {land['size']}
        - Rent: {land['rent']}
        - Duration: {land['duration']}

        Terms and Conditions:
        1. The Tenant shall use the land for agricultural purposes only.
        2. The Tenant shall pay rent as agreed.
        3. The Tenant is responsible for crop cultivation and risks.
        4. The Owner acts only as facilitator.

        This is a digital agreement preview. Actual agreement will be generated after approval.
        """
    }
    return render(request, 'agrolease/agreement_preview.html', context)