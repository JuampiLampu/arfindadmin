from flask import Flask, jsonify, redirect, render_template, request, session, url_for
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv
import os
from functools import wraps
from datetime import datetime
from firebase_admin import storage
from PIL import Image
import io

from auth_config import pyrebase_auth, config
from api_client import APIClient


load_dotenv()


if not firebase_admin._apps:
    cred = credentials.Certificate({
        "type": "service_account",
        "project_id": os.getenv("FIREBASE_PROJECT_ID"),
        "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
        "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
        "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
        "client_id": os.getenv("FIREBASE_CLIENT_ID"),
        "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
        "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
        "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT"),
        "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT"),
    })
    firebase_admin.initialize_app(cred, {
        "storageBucket": "arfind.appspot.com"
    })

db = firestore.client()
bucket = storage.bucket()


def get_authorization_headers():
    id_token = session.get("idToken")
    return {"Authorization": f"Bearer {id_token}"} if id_token else {}

api_client = APIClient(base_url="https://arfindfranco-t22ijacwda-uc.a.run.app")


app = Flask(__name__)
app.secret_key = 'arfind'



# LOGIN
@app.route('/', methods=['GET', 'POST'])
def handle_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        try:
            user = pyrebase_auth.sign_in_with_email_and_password(username, password)
            id_token = user['idToken']
            print(f"ID Token generado: {id_token}")

            empleados_ref = db.collection('empleados')
            query = empleados_ref.where('email', '==', username).stream()

            empleado_data = None
            for doc in query:
                empleado_data = doc.to_dict()
                break

            if empleado_data:
                session['nombreEmpleado'] = empleado_data['nombre']
                session['is_admin'] = empleado_data['is_admin']
                session['idToken'] = id_token

                return jsonify({"message": "Inicio de sesión exitoso", "idToken": id_token}), 200
            else:
                return jsonify({"message": "Empleado no encontrado en Firestore"}), 404

        except Exception as e:
            print(f"Error durante el inicio de sesión: {str(e)}")
            return jsonify({"message": "Error durante el inicio de sesión. Intente nuevamente."}), 401

    return render_template('login.html')



def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'idToken' not in session:
            return redirect(url_for('handle_login'))

        return f(*args, **kwargs)

    return decorated_function


# LOGOUT
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('handle_login'))


# DASHBOARD para Administradores
@app.route('/dashboard')
@login_required
def dashboard():
    if not session.get('is_admin', 0):
        return redirect(url_for('dashboard2'))

    try:
        dispositivos = db.collection('dispositivos').stream()
        planes = db.collection('planes').stream()
        empleados = db.collection('empleados').stream()
        admins = db.collection('empleados').where('is_admin', '==', True).stream()
        pedidos_entregados = db.collection('pedidos').where('is_entregado', '==', True).stream()
        pedidos_no_entregados = db.collection('pedidos').where('is_entregado', '==', False).stream()

        return render_template(
            'index.html',
            total_dispositivos=len(list(dispositivos)),
            total_planes=len(list(planes)),
            total_empleados=len(list(empleados)),
            total_admins=len(list(admins)),
            pedidos_entregados=len(list(pedidos_entregados)),
            pedidos_no_entregados=len(list(pedidos_no_entregados)),
            is_admin=True
        )
    except Exception as e:
        print(f"Error cargando dashboard admin: {e}")
        return "Error cargando dashboard admin", 500


# DASHBOARD para No Administradores
@app.route('/dashboard2')
@login_required
def dashboard2():
    try:
        db = firestore.client()
        pedidos_ref = db.collection('pedidos').stream()
        pedidos = [pedido.to_dict() for pedido in pedidos_ref]

        pedidos_totales = len(pedidos)
        pedidos_entregados = len([p for p in pedidos if p.get('is_entregado')])
        pedidos_no_entregados = len([p for p in pedidos if not p.get('is_entregado')])

        return render_template(
            'index.html',
            pedidos_totales=pedidos_totales,
            pedidos_entregados=pedidos_entregados,
            pedidos_no_entregados=pedidos_no_entregados,
            is_admin=False
        )
    except Exception as e:
        print(f"Error al cargar el Dashboard de empleados: {e}")
        return render_template(
            'index.html',
            pedidos_totales=0,
            pedidos_entregados=0,
            pedidos_no_entregados=0,
            is_admin=False
        )

# RUTA PARA MOSTRAR EMPLEADOS
@app.route('/empleados', methods=['GET'])
@login_required
def empleados():
    try:
        response = api_client.get("empleados/getEmpleados")

        if response:
            empleados = response.get("data", [])
            return render_template('tb-empleados.html', empleados=empleados)
        else:
            return jsonify({"message": "Error al obtener empleados"}), 500
    except Exception as e:
        print(f"Error inesperado en empleados: {e}")
        return jsonify({"message": "Error interno del servidor"}), 500

# RUTA PARA AGREGAR EMPLEADO
@app.route('/empleados/agregar', methods=['GET', 'POST'])
@login_required
def agregar_empleado():
    error_message = None
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('correo')
        password = request.form.get('password')
        is_admin = request.form.get('is_admin')

        print(f"Datos recibidos: Nombre={nombre}, Email={email}, Password={password}, is_admin={is_admin}")

        if not nombre or not email or not password or not is_admin:
            error_message = "Todos los campos son obligatorios."
            return render_template('agregar-empleado.html', error_message=error_message)

        if len(password) < 6:
            error_message = "La contraseña debe tener al menos 6 caracteres."
            return render_template('agregar-empleado.html', error_message=error_message)

        try:
            is_admin = is_admin.lower() == 'true'
            payload = {
                'nombre': nombre,
                'email': email,
                'password': password,
                'is_admin': is_admin
            }
            print(f"Payload enviado a la API: {payload}")

            response = api_client.post('empleados/createEmpleado', json=payload)
            if response:
                return redirect(url_for('empleados'))
            else:
                error_message = "Error al agregar el empleado."
        except Exception as e:
            print(f"Error al agregar empleado: {e}")
            error_message = "Ocurrió un error al procesar la solicitud."

    return render_template('agregar-empleado.html', error_message=error_message)


# RUTA PARA MODIFICAR EMPLEADO
@app.route('/empleados/editar/<string:id_empleado>', methods=['GET', 'POST'])
@login_required
def modificar_empleado(id_empleado):
    error_message = None

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        email = request.form.get('correo')
        is_admin = request.form.get('is_admin')
        password = request.form.get('password')

        try:
            is_admin = is_admin.lower() == 'true'
            payload = {
                'id': id_empleado,
                'nombre': nombre,
                'email': email,
                'is_admin': is_admin
            }
            if password:
                if len(password) < 6:
                    error_message = "La contraseña debe tener al menos 6 caracteres."
                    return render_template('editar-empleado.html', error_message=error_message)
                payload['password'] = password
            print(f"Payload enviado a la API para editar: {payload}")

            response = api_client.put('empleados/updateEmpleado', json=payload)

            if response:
                return redirect(url_for('empleados'))
            else:
                error_message = response.get('message', 'Error desconocido al editar empleado.')

        except Exception as e:
            print(f"Error al editar empleado: {e}")
            return "Error al procesar la solicitud", 500

    try:
        print("Obteniendo todos los empleados para buscar por ID...")
        response = api_client.get('empleados/getEmpleados')

        if response and 'data' in response:
            empleados = response['data']
            empleado_data = next((emp for emp in empleados if emp['id'] == id_empleado), None)

            if empleado_data:
                print(f"Datos obtenidos del empleado: {empleado_data}")
                return render_template('editar-empleado.html', empleado=empleado_data, error_message=error_message)
            else:
                return f"No se encontró un empleado con ID: {id_empleado}", 404
        else:
            return "Error al obtener empleados desde la API", 500

    except Exception as e:
        print(f"Error al cargar datos del empleado: {e}")
        return "Error interno al cargar los datos del empleado", 500




# RUTA PARA ELIMINAR EMPLEADO
@app.route('/empleados/eliminar/<string:id_empleado>', methods=['POST'])
@login_required
def eliminar_empleado(id_empleado):
    try:
        payload = {'id': id_empleado}
        print(f"Payload enviado a la API para eliminar: {payload}")

        response = api_client.delete('empleados/deleteEmpleado', json=payload)

        if response and response.get('message') == 'Empleado eliminado con éxito':
            print("Empleado eliminado correctamente.")
            return redirect(url_for('empleados', mensaje="Empleado eliminado con éxito"))
        else:
            error_message = response.get('message', 'Error desconocido al eliminar empleado.')
            print(f"Error al eliminar empleado: {error_message}")
            return redirect(url_for('empleados', mensaje=error_message))

    except Exception as e:
        print(f"Error al eliminar empleado: {e}")
        return redirect(url_for('empleados', mensaje="Error al procesar la solicitud"))

# PEDIDOS
@app.route('/pedidos', methods=['GET'])
@login_required
def pedidos():
    pedidos_list = []
    try:
        pedidos_ref = db.collection('pedidos')
        snapshot = pedidos_ref.stream()

        for doc in snapshot:
            pedido = doc.to_dict()
            pedido['id'] = doc.id
            fecha_solicitud = pedido.get('fecha_solicitud')

            if fecha_solicitud:
                from datetime import datetime
                if isinstance(fecha_solicitud, datetime):
                    timestamp = fecha_solicitud
                elif hasattr(fecha_solicitud, 'seconds'):
                    timestamp = datetime.fromtimestamp(fecha_solicitud.seconds)
                else:
                    timestamp = None

                pedido['createdAt'] = timestamp.strftime('%Y-%m-%d %H:%M:%S') if timestamp else 'No disponible'
            else:
                pedido['createdAt'] = 'No disponible'

            pedido['status'] = 'Entregado' if pedido.get('is_entregado', False) else 'No Entregado'
            pedido['prod'] = pedido.get('producto_id', 'No especificado')
            pedido['userId'] = pedido.get('usuario_id', 'No especificado')

            pedidos_list.append(pedido)

        print(f"Pedidos obtenidos: {pedidos_list}")
    except Exception as e:
        print(f"Error al obtener pedidos: {e}")

    return render_template('tb-pedido.html', pedidos=pedidos_list, is_admin=session.get('is_admin', False))


@app.route('/modificar_pedido/<id_pedido>', methods=['GET', 'POST'])
@login_required
def modificar_pedido(id_pedido):
    try:
        if request.method == 'POST':
            data = {
                "direccion": request.form.get('direccion'),
                "producto_id": request.form.get('producto_id'),
                "is_entregado": request.form.get('is_entregado') == 'true'
            }

            print(f"Datos recibidos para actualizar el pedido {id_pedido}: {data}")

            db.collection('pedidos').document(id_pedido).update(data)
            return redirect(url_for('pedidos'))

        pedido_doc = db.collection('pedidos').document(id_pedido).get()
        if pedido_doc.exists:
            pedido = pedido_doc.to_dict()
            return render_template('editar-pedido.html', pedido=pedido, id_pedido=id_pedido)
        else:
            return "Pedido no encontrado", 404
    except Exception as e:
        print(f"Error al modificar el pedido: {e}")
        return "Error interno del servidor", 500



@app.route('/eliminar_pedido/<string:id_pedido>', methods=['POST'])
def eliminar_pedido(id_pedido):
    pedido_ref = db.collection('pedidos').document(id_pedido)

    try:
        pedido_ref.delete()
        print(f"Pedido {id_pedido} eliminado con éxito.")
        return redirect(url_for('pedidos'))
    except Exception as e:
        print(f"Error al eliminar el pedido {id_pedido}: {e}")
        return "Error al eliminar el pedido", 500

# PRODUCTOS
@app.route('/productos', methods=['GET'])
@login_required
def productos():
    error_message = None
    productos = []

    try:
        productos_data = db.collection('productos').stream()
        productos = [{'id': doc.id, **doc.to_dict()} for doc in productos_data]
    except Exception as e:
        error_message = f"Error al obtener los productos: {e}"
        print(error_message)

    return render_template('tb-productos.html', productos=productos, error_message=error_message)

@app.route('/agregar_producto', methods=['GET', 'POST'])
@login_required
def agregar_producto():
    error_message = None
    if request.method == 'POST':
        try:
            titulo = request.form.get('titulo')
            descripcion = request.form.get('descripcion')
            precio = request.form.get('precio')
            imagen = request.files.get('imagen')
            tiny_descripcion = request.form.get('tinyDescripcion')

            if not (titulo and descripcion and precio and imagen):
                error_message = "Todos los campos obligatorios deben completarse."
                raise ValueError(error_message)

            filename, ext = os.path.splitext(imagen.filename)
            ext = ext.lower()
            if ext not in ['.png', '.jpg', '.jpeg']:
                ext = '.png'
            converted_filename = f"{filename}{ext}"

            image = Image.open(imagen)
            image_io = io.BytesIO()
            image.save(image_io, format='PNG' if ext == '.png' else 'JPEG')
            image_io.seek(0)

            blob = bucket.blob(f"productos/{converted_filename}")
            blob.upload_from_file(image_io, content_type=f"image/{'png' if ext == '.png' else 'jpeg'}")
            blob.make_public()
            imagen_url = blob.public_url

            producto_data = {
                'titulo': titulo,
                'descripcion': descripcion,
                'precio': float(precio),
                'imagen': imagen_url,
                'tiny_descripcion': tiny_descripcion,
                'fecha_creacion': datetime.utcnow()
            }
            db.collection('productos').add(producto_data)

            return redirect(url_for('productos'))
        except Exception as e:
            error_message = f"Error al procesar el formulario: {e}"
            print(error_message)

    return render_template('agregar-producto.html', error_message=error_message)


@app.route('/modificar_producto/<string:id_producto>', methods=['GET', 'POST'])
@login_required
def modificar_producto(id_producto):
    error_message = None
    producto_ref = db.collection('productos').document(id_producto)

    if request.method == 'POST':
        titulo = request.form.get('titulo')
        descripcion = request.form.get('descripcion')
        precio = request.form.get('precio')
        imagen = request.files.get('imagen')  # Archivo subido
        tiny_descripcion = request.form.get('tinyDescripcion')

        try:
            updates = {
                'titulo': titulo,
                'descripcion': descripcion,
                'precio': float(precio) if precio else None,
                'tiny_descripcion': tiny_descripcion,
                'ult_actualizacion': datetime.utcnow()
            }

            if imagen:
                filename, ext = os.path.splitext(imagen.filename)
                ext = ext.lower()
                if ext not in ['.png', '.jpg', '.jpeg']:
                    ext = '.png'
                converted_filename = f"{filename}{ext}"

                image = Image.open(imagen)
                image_io = io.BytesIO()
                image.save(image_io, format='PNG' if ext == '.png' else 'JPEG')
                image_io.seek(0)

                blob = bucket.blob(f"productos/{converted_filename}")
                blob.upload_from_file(image_io, content_type=f"image/{'png' if ext == '.png' else 'jpeg'}")
                blob.make_public()
                updates['imagen'] = blob.public_url

            producto_ref.update(updates)
            return redirect(url_for('productos'))
        except Exception as e:
            error_message = f"Error al actualizar el producto: {str(e)}"
            print(error_message)

    producto = producto_ref.get()
    if producto.exists:
        producto_data = {'id': producto_ref.id, **producto.to_dict()}
    else:
        return "Producto no encontrado", 404

    return render_template('editar-producto.html', producto=producto_data, error_message=error_message)




@app.route('/upload_image', methods=['POST'])
def upload_image():
    if 'imagen' not in request.files:
        return "No se subió ningún archivo", 400

    file = request.files['imagen']
    if file.filename == '':
        return "Nombre del archivo vacío", 400

    bucket = storage.bucket()
    blob = bucket.blob(f"imagenes/{file.filename}")
    blob.upload_from_file(file)

    return blob.public_url

@app.route('/eliminar_producto/<string:id_producto>', methods=['POST'])
@login_required
def eliminar_producto(id_producto):
    try:
        producto_ref = db.collection('productos').document(id_producto)

        producto_ref.delete()
        print(f"Producto {id_producto} eliminado de Firestore.")
        return redirect(url_for('productos'))
    except Exception as e:
        print(f"Error al eliminar el producto {id_producto}: {e}")
        return redirect(url_for('productos', mensaje="Error al eliminar el producto"))


# DISPOSITIVOS
@app.route('/dispositivos', methods=['GET'])
@login_required
def dispositivos():
    error_message = None
    dispositivos = []

    try:
        response = api_client.get('dispositivos/getAllDispositivos')

        if isinstance(response, list):
            dispositivos = response
        else:
            error_message = "La API devolvió una respuesta inesperada."
    except Exception as e:
        error_message = f"Error al obtener dispositivos: {str(e)}"

    return render_template('tb-dispositivo.html', dispositivos=dispositivos, error_message=error_message)


@app.route('/agregar_dispositivo', methods=['GET', 'POST'])
@login_required
def agregar_dispositivo():
    error_message = None

    if request.method == 'POST':
        numero_telefonico = request.form.get('numero_telefonico')
        tipo_producto = request.form.get('tipo_producto')

        if not numero_telefonico or not tipo_producto:
            error_message = "Todos los campos son obligatorios."
        else:
            payload = {
                'numero_telefonico': numero_telefonico,
                'tipo_producto': tipo_producto
            }

            try:
                response = api_client.post('dispositivos/createDispositivo', json=payload)
                if response and response.get('message') == 'Dispositivo creado exitosamente':
                    return redirect(url_for('dispositivos'))
                else:
                    error_message = response.get('message', 'Error al agregar el dispositivo.')
            except Exception as e:
                error_message = f"Error al agregar el dispositivo: {str(e)}"

    productos_query = db.collection('productos').stream()
    productos = [{'id': doc.id, 'titulo': doc.to_dict().get('titulo')} for doc in productos_query]

    return render_template('agregar-dispositivo.html', productos=productos, error_message=error_message)


@app.route('/modificar_dispositivo/<string:id_dispositivo>', methods=['GET', 'POST'])
@login_required
def modificar_dispositivo(id_dispositivo):
    error_message = None
    dispositivo = {}

    if request.method == 'POST':
        numero_telefonico = request.form.get('numero_telefonico')
        plan_id = request.form.get('plan_id')
        updated_data = {}
        if numero_telefonico:
            updated_data['numero_telefonico'] = numero_telefonico
        if plan_id and plan_id != "N/A":
            updated_data['plan_id'] = plan_id
        if not updated_data:
            error_message = "No se detectaron cambios para actualizar."
        else:
            payload = {
                'deviceId': id_dispositivo,
                'updatedData': updated_data
            }

            try:
                response = api_client.put('dispositivos/updateDispositivo', json=payload)
                if response and response.get('message') == 'Dispositivo actualizado exitosamente':
                    return redirect(url_for('dispositivos'))
                else:
                    error_message = response.get('message', 'Error al actualizar el dispositivo.')
            except Exception as e:
                error_message = f"Error al actualizar el dispositivo: {str(e)}"

    try:
        response = api_client.get('dispositivos/getAllDispositivos')
        if response:
            dispositivos = response
            dispositivo = next((d for d in dispositivos if d['id'] == id_dispositivo), None)

            if dispositivo:
                usuarios_invitados = dispositivo.get('usuarios_invitados', [])
                usuario_id = dispositivo.get('usuario_id')
                emails_invitados = []
                for user_id in usuarios_invitados:
                    user_response = api_client.get(f'usuarios/{user_id}')
                    if user_response and user_response.get('correo'):
                        emails_invitados.append(user_response['correo'])
                    else:
                        emails_invitados.append(f"Usuario ID {user_id} no disponible")

                dispositivo['usuarios_invitados'] = emails_invitados
                if usuario_id:
                    user_response = api_client.get(f'usuarios/{usuario_id}')
                    dispositivo['usuario_id'] = user_response.get('correo', 'Correo no disponible')
            else:
                error_message = "Dispositivo no encontrado."
        else:
            error_message = "Error al obtener los dispositivos."
    except Exception as e:
        error_message = f"Error al cargar los datos del dispositivo: {str(e)}"

    return render_template('editar-dispositivo.html', dispositivo=dispositivo, error_message=error_message)


@app.route('/eliminar_dispositivo/<string:id_dispositivo>', methods=['POST'])
@login_required
def eliminar_dispositivo(id_dispositivo):
    try:
        payload = {'deviceId': id_dispositivo}
        print(f"Payload enviado a la API para eliminar: {payload}")

        response = api_client.delete('dispositivos/deleteDispositivo', json=payload)

        if response and response.get('message') == 'Dispositivo eliminado exitosamente':
            print("Dispositivo eliminado correctamente.")
            return redirect(url_for('dispositivos'))
        else:
            error_message = response.get('message', 'Error al eliminar el dispositivo.')
            print(f"Error al eliminar dispositivo: {error_message}")
            return redirect(url_for('dispositivos', mensaje=error_message))
    except Exception as e:
        print(f"Error al eliminar dispositivo: {e}")
        return redirect(url_for('dispositivos', mensaje="Error al procesar la solicitud"))


@app.route('/planes', methods=['GET'])
@login_required
def planes():
    mensaje = request.args.get('mensaje', None)
    try:
        planes_query = db.collection('planes').stream()
        planes = [{'id': doc.id, **doc.to_dict()} for doc in planes_query]
        return render_template('tb-planes.html', planes=planes, mensaje=mensaje)
    except Exception as e:
        print(f"Error al obtener planes: {e}")
        return render_template('tb-planes.html', planes=[], mensaje="Error al obtener los planes.")


@app.route('/planes/agregar', methods=['GET', 'POST'])
@login_required
def agregar_plan():
    error_message = None
    if request.method == 'POST':
        try:
            nombre = request.form.get('nombre')
            precio = float(request.form.get('precio'))
            descripcion = request.form.get('descripcion')
            refresco = int(request.form.get('refresco'))
            cantidad_compartidos = int(request.form.get('cantidad_compartidos'))
            imagen = request.files.get('imagen')

            if not all([nombre, precio, descripcion, refresco, cantidad_compartidos, imagen]):
                raise ValueError("Todos los campos son obligatorios.")

            filename, ext = os.path.splitext(imagen.filename)
            ext = ext.lower()
            if ext not in ['.png', '.jpg', '.jpeg']:
                ext = '.png'
            converted_filename = f"planes/{filename}{ext}"

            image = Image.open(imagen)
            image_io = io.BytesIO()
            image.save(image_io, format='PNG' if ext == '.png' else 'JPEG')
            image_io.seek(0)
            blob = bucket.blob(converted_filename)
            blob.upload_from_file(image_io, content_type=f"image/{'png' if ext == '.png' else 'jpeg'}")
            blob.make_public()
            imagen_url = blob.public_url
            plan_data = {
                'nombre': nombre,
                'precio': precio,
                'descripcion': descripcion,
                'refresco': refresco,
                'cantidad_compartidos': cantidad_compartidos,
                'imagen': imagen_url,
                'fecha_creacion': datetime.utcnow()
            }
            db.collection('planes').add(plan_data)

            return redirect(url_for('planes', mensaje="Plan agregado con éxito"))
        except Exception as e:
            error_message = f"Error al agregar el plan: {e}"
            print(error_message)

    return render_template('agregar-planes.html', error_message=error_message)


@app.route('/planes/editar/<string:id_plan>', methods=['GET', 'POST'])
@login_required
def modificar_plan(id_plan):
    error_message = None
    plan_ref = db.collection('planes').document(id_plan)

    if request.method == 'POST':
        try:
            nombre = request.form.get('nombre')
            precio = float(request.form.get('precio'))
            descripcion = request.form.get('descripcion')
            refresco = int(request.form.get('refresco'))
            cantidad_compartidos = int(request.form.get('cantidad_compartidos'))
            imagen = request.files.get('imagen')

            updates = {
                'nombre': nombre,
                'precio': precio,
                'descripcion': descripcion,
                'refresco': refresco,
                'cantidad_compartidos': cantidad_compartidos,
                'ult_actualizacion': datetime.utcnow()
            }
            if imagen:
                filename, ext = os.path.splitext(imagen.filename)
                ext = ext.lower()
                if ext not in ['.png', '.jpg', '.jpeg']:
                    ext = '.png'
                converted_filename = f"planes/{filename}{ext}"

                image = Image.open(imagen)
                image_io = io.BytesIO()
                image.save(image_io, format='PNG' if ext == '.png' else 'JPEG')
                image_io.seek(0)
                blob = bucket.blob(converted_filename)
                blob.upload_from_file(image_io, content_type=f"image/{'png' if ext == '.png' else 'jpeg'}")
                blob.make_public()
                updates['imagen'] = blob.public_url
            plan_ref.update(updates)
            return redirect(url_for('planes', mensaje="Plan actualizado con éxito"))
        except Exception as e:
            error_message = f"Error al actualizar el plan: {e}"
            print(error_message)

    plan = plan_ref.get()
    if plan.exists:
        plan_data = {'id': plan_ref.id, **plan.to_dict()}
    else:
        return "Plan no encontrado", 404

    return render_template('editar-planes.html', plan=plan_data, error_message=error_message)

@app.route('/planes/eliminar/<string:id_plan>', methods=['POST'])
@login_required
def eliminar_plan(id_plan):
    try:
        plan_ref = db.collection('planes').document(id_plan)
        plan_ref.delete()
        print(f"Plan {id_plan} eliminado de Firestore.")
        return redirect(url_for('planes', mensaje="Plan eliminado con éxito"))
    except Exception as e:
        print(f"Error al eliminar el plan {id_plan}: {e}")
        return redirect(url_for('planes', mensaje="Error al eliminar el plan"))

@app.route('/tiponotificaciones', methods=['GET'])
@login_required
def tiponotificaciones():
    try:
        response = api_client.get('notificaciones/getTiposNotificaciones')
        if response:
            tipos_notificaciones = response
            return render_template('tb-tipo_notificaciones.html', tipos_notificaciones=tipos_notificaciones)
        else:
            return render_template('tb-tipo_notificaciones.html', tipos_notificaciones=[], mensaje="Error al obtener los datos.")
    except Exception as e:
        print(f"Error al obtener tipos de notificación: {e}")
        return render_template('tb-tipo_notificaciones.html', tipos_notificaciones=[], mensaje="Error interno del servidor.")




@app.route('/tiponotificaciones/agregar', methods=['GET', 'POST'])
@login_required
def agregar_tiponotificacion():
    if request.method == 'POST':
        try:
            tipo = request.form.get('tipo')
            mensaje_plantilla = request.form.get('mensaje_plantilla')

            if not (tipo and mensaje_plantilla):
                raise ValueError("Todos los campos son obligatorios.")

            payload = {
                'id_tipo_notificacion': str(datetime.utcnow().timestamp()),
                'tipo': tipo,
                'mensaje_plantilla': mensaje_plantilla,
            }
            response = api_client.post('notificaciones/createTipoNotificacion', json=payload)

            if response:
                return redirect(url_for('tiponotificaciones'))
            else:
                raise ValueError("Error al agregar el tipo de notificación.")
        except Exception as e:
            print(f"Error al agregar tipo de notificación: {e}")
    return render_template('agregar-tiponotificaciones.html')


@app.route('/tiponotificaciones/editar/<string:id_tipo>', methods=['GET', 'POST'])
@login_required
def editar_tiponotificacion(id_tipo):
    if request.method == 'POST':
        try:
            tipo = request.form.get('tipo')
            mensaje_plantilla = request.form.get('mensaje_plantilla')

            if not (tipo or mensaje_plantilla):
                raise ValueError("Debe proporcionar al menos un campo para actualizar.")

            payload = {
                'id': id_tipo,
                'tipo': tipo,
                'mensaje_plantilla': mensaje_plantilla,
            }
            response = api_client.put('notificaciones/updateTipoNotificacion', json=payload)

            if response:
                return redirect(url_for('tiponotificaciones'))
            else:
                raise ValueError("Error al editar el tipo de notificación.")
        except Exception as e:
            print(f"Error al editar tipo de notificación: {e}")

    response = api_client.get('notificaciones/getTiposNotificaciones')
    tipo = next((t for t in response if t['id'] == id_tipo), None) if response else None
    return render_template('editar-tiponotificaciones.html', tipo=tipo)


@app.route('/tiponotificaciones/eliminar/<string:id_tipo>', methods=['POST'])
@login_required
def eliminar_tiponotificacion(id_tipo):
    try:
        payload = {'id': id_tipo}
        response = api_client.delete('notificaciones/deleteTipoNotificacion', json=payload)

        if response:
            return redirect(url_for('tiponotificaciones'))
        else:
            raise ValueError("Error al eliminar el tipo de notificación.")
    except Exception as e:
        print(f"Error al eliminar tipo de notificación: {e}")
        return redirect(url_for('tiponotificaciones', mensaje="Error interno del servidor."))


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(value):
    try:
        return datetime.fromtimestamp(value).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return "N/A"

if __name__ == '__main__':
    app.run(debug=True)
