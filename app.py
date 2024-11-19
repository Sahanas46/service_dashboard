from flask import Flask, flash, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from flask_migrate import Migrate

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:admin@localhost/vehicles'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)  # Initialize Flask-Migrate

app.secret_key = 'your_secret_key'  # Make sure to set a secret key

def format_time(dt):
    """Utility function to format time in 'HH:MM:SS' format."""
    if dt:
        return dt.strftime("%H:%M:%S")
    return 'N/A'

@app.context_processor
def utility_processor():
    """Provide format_time function globally to templates."""
    return dict(format_time=format_time)

class Vehicle(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(50), nullable=False, unique=True)
    entry_time = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # Set entry time when added
    exit_time = db.Column(db.DateTime, nullable=True)
    service_time = db.Column(db.Float, nullable=True, default=0)
    in_service = db.Column(db.Boolean, default=False)
    minor_service_active = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default='new')

    comments = db.relationship('ServiceComment', backref='vehicle', lazy=True)

    def __repr__(self):
        return f'<Vehicle {self.plate_number}>'

class CompletedService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate_number = db.Column(db.String(50), nullable=False)
    service_time = db.Column(db.Float, nullable=False)
    exit_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='new')

    comments = db.relationship('ServiceComment', backref='completed_service', lazy=True)

    def __repr__(self):
        return f'<CompletedService {self.plate_number}>'

class ServiceComment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    completed_service_id = db.Column(db.Integer, db.ForeignKey('completed_service.id'), nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicle.id'), nullable=True)
    comment = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<ServiceComment {self.comment}>'

with app.app_context():
    db.create_all()  # Ensure tables are created

@app.route('/')
def index():
    vehicles = Vehicle.query.order_by(Vehicle.id).all()
    current_time = datetime.now()

    in_service_vehicles = [v for v in vehicles if v.in_service]
    completed_services = CompletedService.query.all()

    # Split vehicles into lanes
    lanes = [[] for _ in range(4)]
    
    # Distribute vehicles across 4 lanes (each lane will take 4 vehicles)
    for i, vehicle in enumerate(vehicles):
        lanes[i % 4].append(vehicle)

    # Check if the vehicle's entry time has exceeded 24 hours
    for vehicle in vehicles:
        vehicle.exceeds_24_hours = (current_time - vehicle.entry_time).total_seconds() > 86400  # 24 hours in seconds

    return render_template('index.html', lanes=lanes, current_time=current_time,
                           in_service_vehicles=in_service_vehicles,
                           completed_services=completed_services)

@app.route('/add', methods=['POST'])
def add_vehicle():
    plate_number = request.form['plate_number']
    
    # Check if a vehicle with the same plate number already exists
    existing_vehicle = Vehicle.query.filter_by(plate_number=plate_number).first()
    if existing_vehicle:
        flash('Vehicle with this plate number already exists.', 'danger')
        return redirect('/')

    # Get all vehicles and count them in each lane
    vehicles = Vehicle.query.all()
    lanes = [[] for _ in range(4)]

    # Distribute vehicles across lanes (4 vehicles per lane)
    for i, vehicle in enumerate(vehicles):
        lanes[i % 4].append(vehicle)

    # Check if all lanes are full (each lane should hold exactly 4 vehicles)
    if all(len(lane) >= 4 for lane in lanes):
        flash('Cannot add vehicle. All lanes are full.', 'danger')
        return redirect('/')

    # Find the first lane that has space
    for lane_index in range(4):
        if len(lanes[lane_index]) < 4:
            new_vehicle = Vehicle(plate_number=plate_number, entry_time=datetime.now())  # Set entry time when added
            db.session.add(new_vehicle)
            db.session.commit()
            break  # Exit the loop after adding the vehicle
    
    return redirect('/')

@app.route('/minor_service/<int:vehicle_id>', methods=['POST'])
def minor_service(vehicle_id):
    vehicle = Vehicle.query.get(vehicle_id)
    if vehicle and not vehicle.in_service:
        vehicle.minor_service_active = True
        db.session.commit()
    return redirect('/')

@app.route('/service/<int:vehicle_id>', methods=['POST'])
def service_vehicle(vehicle_id):
    vehicle = Vehicle.query.get(vehicle_id)
    if vehicle and not vehicle.in_service and not vehicle.minor_service_active:
        # Check if any vehicle is currently in major service
        if not Vehicle.query.filter_by(in_service=True).first():
            vehicle.in_service = True
            db.session.commit()  # No need to change the entry_time here
    return redirect('/')

@app.route('/complete/<int:vehicle_id>', methods=['POST'])
def complete_service(vehicle_id):
    vehicle = Vehicle.query.get(vehicle_id)
    if vehicle:
        time_difference = datetime.now() - vehicle.entry_time  # Use entry_time for calculation
        total_seconds = time_difference.total_seconds()

        # Calculate hours, minutes, and seconds
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)

        # Format the service time as "HH:MM:SS"
        service_time_formatted = f"{hours:02}:{minutes:02}:{seconds:02}"

        if vehicle.in_service:
            vehicle.service_time += total_seconds / 3600  # Keep total service time in hours
            vehicle.exit_time = datetime.now()
            vehicle.in_service = False
        elif vehicle.minor_service_active:
            vehicle.service_time += total_seconds / 3600
            vehicle.exit_time = datetime.now()
            vehicle.minor_service_active = False

        completed_service = CompletedService(
            plate_number=vehicle.plate_number,
            service_time=total_seconds / 3600,  # Store as float for future calculations
            exit_time=datetime.now(),
            status=vehicle.status
        )

        db.session.add(completed_service)

        # Add comments for the completed service
        for comment in vehicle.comments:
            if comment:  # Ensure the comment is valid
                service_comment = ServiceComment(
                    completed_service_id=completed_service.id,  # Use the committed ID
                    comment=comment.comment
                )
                db.session.add(service_comment)

        # Clear vehicle's exit time and commit
        vehicle.exit_time = datetime.now()
        db.session.commit()

        flash(f'Service completed in {service_time_formatted}.', 'success')

    return redirect('/')

@app.route('/update_status/<int:vehicle_id>', methods=['POST'])
def update_status(vehicle_id):
    vehicle = Vehicle.query.get(vehicle_id)
    if vehicle:
        new_status = request.form['status']
        
        # Update the vehicle status
        vehicle.status = new_status
        
        # Check if the status is 'completed'
        if new_status == 'completed':
            time_difference = datetime.now() - vehicle.entry_time
            service_time = time_difference.total_seconds() / 3600  # Convert seconds to hours
            
            # Create a new CompletedService entry
            completed_service = CompletedService(
                plate_number=vehicle.plate_number,
                service_time=service_time,
                exit_time=datetime.now(),  # Set exit time to now
                status=vehicle.status  # Add the status to completed service
            )
            db.session.add(completed_service)
            db.session.delete(vehicle)  # Remove the vehicle from active list
        
        db.session.commit()
        flash('Vehicle status updated successfully.', 'success')
    else:
        flash('Vehicle not found.', 'danger')
    return redirect('/')

@app.route('/submit_major_service', methods=['POST'])
def submit_major_service():
    vehicle_id = request.form['vehicle_id']
    service_comment = request.form['service_comment']

    # Create a new comment instance
    new_comment = ServiceComment(vehicle_id=vehicle_id, comment=service_comment)
    
    db.session.add(new_comment)
    db.session.commit()

    flash('Comment added successfully!', 'success')
    return redirect('/')

def format_time(dt):
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M:%S")  # Format as 'YYYY-MM-DD HH:MM:SS'
    return 'N/A'

if __name__ == '__main__':
    app.run(debug=True)