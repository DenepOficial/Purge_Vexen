import os
import json
import asyncio
from datetime import datetime, timedelta, timezone
from threading import Thread
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask

# =========================
# CONFIGURACION DE FLASK
# =========================
app = Flask("")

@app.route("/")
def home():
    return "Bot de Limpieza Online!", 200

def run_server():
    port = int(os.getenv("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# =========================
# SERVIDORES PERMITIDOS (WHITELIST)
# =========================
BASE_DIR = os.path.dirname(__file__)
WHITELIST_FILE = os.path.join(BASE_DIR, "whitelist.json")
SUPPORT_DISCORD = os.getenv("SUPPORT_DISCORD", "denepoficial")
ACCESS_REQUEST_URL = os.getenv("ACCESS_REQUEST_URL", "https://discord.com/channels/@denepoficial")

# =========================
# ROLES PERMITIDOS COMANDOS PRIVADOS
# =========================

ROLES_FILE = os.path.join(BASE_DIR, "roles_permitidos.json")


def load_allowed_roles() -> set[int]:
    if not os.path.exists(ROLES_FILE):
        print("roles_permitidos.json no existe.")
        return set()

    try:
        with open(ROLES_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        roles = set()

        for role_id in data.get("role_ids", []):
            try:
                roles.add(int(role_id))
            except ValueError:
                print(f"ID de rol invalido: {role_id}")

        return roles

    except Exception as e:
        print(f"Error cargando roles permitidos: {e}")
        return set()


def save_allowed_roles(roles: set[int]):
    try:
        with open(ROLES_FILE, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "role_ids": [
                        str(role_id) for role_id in roles
                    ]
                },
                file,
                indent=4
            )

    except Exception as e:
        print(f"Error guardando roles permitidos: {e}")


def has_allowed_role(member: discord.Member) -> bool:
    allowed_roles = load_allowed_roles()

    user_roles = {
        role.id for role in member.roles
    }

    return bool(
        user_roles.intersection(allowed_roles)
    )

def can_use_cleanup(member: discord.Member) -> bool:
    # Administradores siempre tienen acceso
    if member.guild_permissions.administrator:
        return True

    # Usuarios con roles autorizados tienen acceso
    return has_allowed_role(member)

def load_whitelisted_guilds() -> set[int]:
    if not os.path.exists(WHITELIST_FILE):
        print("whitelist.json no existe. Ningun servidor esta autorizado.")
        return set()
    try:
        with open(WHITELIST_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        raw_guilds = data.get("guild_ids", data if isinstance(data, list) else [])
        guild_ids = set()
        for raw_id in raw_guilds:
            try:
                guild_ids.add(int(raw_id))
            except ValueError:
                print(f"Guild ID invalido en whitelist.json: {raw_id}")
        return guild_ids
    except Exception as e:
        print(f"Error cargando whitelist.json: {e}")
        return set()

def is_whitelisted(guild_id: int | None) -> bool:
    return guild_id is not None and guild_id in load_whitelisted_guilds()

# =========================
# CONFIGURACION DEL BOT
# =========================
# NOTA: Para purgar/borrar mensajes antiguos, el bot NECESITA obligatoriamente 
# el intent de Message Content y permisos de Administrador o Gestionar Mensajes en Discord.
intents = discord.Intents.default()
intents.message_content = True 

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# =========================
# CANALES VINCULADOS Y CONFIGURACIÓN
# =========================
LINKED_CHANNELS_FILE = os.path.join(BASE_DIR, "linked_channels.json")
# Estructura interna: { "guild_id": { "channel_id": { "hours": int, "last_clean": "ISO_TIMESTAMP" } } }
linked_channels: dict[str, dict[str, dict]] = {}

def load_linked_channels():
    global linked_channels
    if not os.path.exists(LINKED_CHANNELS_FILE):
        linked_channels = {}
        return
    try:
        with open(LINKED_CHANNELS_FILE, "r", encoding="utf-8") as file:
            linked_channels = json.load(file)
    except Exception as e:
        print(f"Error cargando canales vinculados: {e}")
        linked_channels = {}

def save_linked_channels():
    try:
        with open(LINKED_CHANNELS_FILE, "w", encoding="utf-8") as file:
            json.dump(linked_channels, file, indent=2)
    except Exception as e:
        print(f"Error guardando canales vinculados: {e}")

# =========================
# WHITELIST: AVISO Y SALIDA
# =========================
async def find_notice_channel(guild: discord.Guild):
    if guild.me is None: return None
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            return channel
    return None

async def leave_if_not_whitelisted(guild: discord.Guild):
    if is_whitelisted(guild.id): return
    print(f"Saliendo de servidor no autorizado: {guild.name} ({guild.id})")
    notice_channel = await find_notice_channel(guild)
    if notice_channel:
        try:
            await notice_channel.send(
                "Este bot es privado y este servidor no esta autorizado.\n"
                f"Para solicitar permisos, comunicate con **{SUPPORT_DISCORD}** en Discord:\n"
                f"{ACCESS_REQUEST_URL}\n\n"
                "Me retirare automaticamente de este servidor."
            )
            await asyncio.sleep(5)
        except Exception as e:
            print(f"No se pudo enviar aviso en {guild.name} ({guild.id}): {e}")
    await guild.leave()

# =========================
# FUNCION AUXILIAR DE LIMPIEZA
# =========================
async def purge_channel(channel: discord.TextChannel) -> int:
    """Borra todos los mensajes posibles de un canal."""
    if not channel.permissions_for(channel.guild.me).manage_messages:
        print(f"Falta permiso 'Gestionar Mensajes' en el canal {channel.name} ({channel.id})")
        return 0
    
    deleted_count = 0
    try:
        # bulk=True usa el borrado rápido (mensajes de menos de 14 días)
        # Si hay mensajes más viejos, los borra uno a uno automáticamente
        deleted = await channel.purge(limit=None, bulk=True)
        deleted_count = len(deleted)
    except Exception as e:
        print(f"Error al purgar el canal {channel.id}: {e}")
    return deleted_count

# =========================
# SLASH COMMANDS
# =========================

@tree.command(
    name="agregar_rol_limpieza",
    description="Agrega un ID de rol autorizado para comandos privados"
)
@app_commands.describe(
    rol_id="ID del rol que tendrá acceso"
)
async def agregar_rol_limpieza(
    interaction: discord.Interaction,
    rol_id: str
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return


    # Verificar que el usuario tenga un rol autorizado
    if not has_allowed_role(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para agregar roles.",
            ephemeral=True
        )
        return


    # Convertir ID recibido
    try:
        rol_id_int = int(rol_id)

    except ValueError:
        await interaction.response.send_message(
            "❌ El ID del rol debe contener solamente números.",
            ephemeral=True
        )
        return


    # Verificar que el rol existe
    rol = interaction.guild.get_role(rol_id_int)

    if rol is None:
        await interaction.response.send_message(
            f"❌ No encontré ningún rol con el ID `{rol_id_int}`.",
            ephemeral=True
        )
        return


    roles_actuales = load_allowed_roles()


    # Evitar duplicados
    if rol_id_int in roles_actuales:
        await interaction.response.send_message(
            f"⚠️ El rol `{rol_id_int}` ya está autorizado.",
            ephemeral=True
        )
        return


    # Guardar nuevo rol
    roles_actuales.add(rol_id_int)

    save_allowed_roles(roles_actuales)


    await interaction.response.send_message(
        "✅ Rol agregado correctamente.\n\n"
        f"🆔 ID: `{rol_id_int}`\n"
        f"🏷️ Nombre actual: `{rol.name}`",
        ephemeral=True
    )

@tree.command(
    name="quitar_rol_limpieza",
    description="Quita un rol autorizado para comandos privados"
)
@app_commands.describe(
    rol_id="ID del rol que será eliminado"
)
async def quitar_rol_limpieza(
    interaction: discord.Interaction,
    rol_id: str
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return

    # Verificar permisos
    if not has_allowed_role(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para quitar roles.",
            ephemeral=True
        )
        return

    # Convertir ID
    try:
        rol_id_int = int(rol_id)

    except ValueError:
        await interaction.response.send_message(
            "❌ El ID del rol debe contener solamente números.",
            ephemeral=True
        )
        return


    roles_actuales = load_allowed_roles()


    # Verificar existencia
    if rol_id_int not in roles_actuales:
        await interaction.response.send_message(
            f"⚠️ El rol `{rol_id_int}` no está autorizado.",
            ephemeral=True
        )
        return


    # Quitar rol
    roles_actuales.remove(rol_id_int)

    save_allowed_roles(roles_actuales)


    rol = interaction.guild.get_role(rol_id_int)


    nombre_rol = rol.name if rol else "Rol eliminado"


    await interaction.response.send_message(
        "✅ Rol eliminado correctamente.\n\n"
        f"🆔 ID: `{rol_id_int}`\n"
        f"🏷️ Nombre: `{nombre_rol}`",
        ephemeral=True
    )

@tree.command(name="link", description="Vincula este canal para que se limpie automaticamente")
@app_commands.describe(horas="Cada cuantas horas se realizara la limpieza automatica (Por defecto: 24)")

async def link(interaction: discord.Interaction, horas: int = 24):

    if not can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
        
    guild_id = str(interaction.guild_id)
    channel_id = str(interaction.channel_id)

    if interaction.guild_id is None:
        await interaction.response.send_message("Este comando solo puede usarse dentro de un servidor.", ephemeral=True)
        return

    if not is_whitelisted(interaction.guild_id):
        await interaction.response.send_message("Este servidor no esta autorizado para usar el bot.", ephemeral=True)
        return

    if horas <= 0:
        await interaction.response.send_message("El intervalo de tiempo debe ser de al menos 1 hora.", ephemeral=True)
        return

    if guild_id not in linked_channels:
        linked_channels[guild_id] = {}

    if channel_id in linked_channels[guild_id]:
        await interaction.response.send_message("Este canal ya se encuentra vinculado. Usa `/configurar` si quieres cambiar las horas.", ephemeral=True)
        return

    # Registrar canal con la hora actual como última limpieza para evitar que borre inmediatamente al linkear
    linked_channels[guild_id][channel_id] = {
        "hours": horas,
        "last_clean": datetime.now(timezone.utc).isoformat()
    }
    save_linked_channels()

    await interaction.response.send_message(
        f"Canal vinculado con exito. Este canal se limpiara por completo cada **{horas} horas**.",
        ephemeral=True
    )

@tree.command(name="unlink", description="Desvincula este canal del sistema de limpieza")

async def unlink(interaction: discord.Interaction):

    if not can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
        
    guild_id = str(interaction.guild_id)
    channel_id = str(interaction.channel_id)

    if interaction.guild_id is None:
        await interaction.response.send_message("Este comando solo puede usarse dentro de un servidor.", ephemeral=True)
        return

    if guild_id in linked_channels and channel_id in linked_channels[guild_id]:
        del linked_channels[guild_id][channel_id]
        # Limpiar el diccionario del servidor si se queda vacío
        if not linked_channels[guild_id]:
            del linked_channels[guild_id]
        save_linked_channels()
        await interaction.response.send_message("Canal desvinculado. Ya no se realizaran limpiezas automaticas aqui.", ephemeral=True)
    else:
        await interaction.response.send_message("Este canal no estaba vinculado.", ephemeral=True)

@tree.command(name="configurar", description="Cambia el intervalo de horas de limpieza para este canal")
@app_commands.describe(horas="Nuevo intervalo de horas para la limpieza automatica")

async def configurar(interaction: discord.Interaction, horas: int):

    if not can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
    
    guild_id = str(interaction.guild_id)
    channel_id = str(interaction.channel_id)

    if interaction.guild_id is None:
        await interaction.response.send_message("Este comando solo puede usarse dentro de un servidor.", ephemeral=True)
        return

    if guild_id in linked_channels and channel_id in linked_channels[guild_id]:
        if horas <= 0:
            await interaction.response.send_message("El intervalo debe ser de al menos 1 hora.", ephemeral=True)
            return

        linked_channels[guild_id][channel_id]["hours"] = horas
        save_linked_channels()
        await interaction.response.send_message(f"Configuracion actualizada. Ahora este canal se limpiara cada **{horas} horas**.", ephemeral=True)
    else:
        await interaction.response.send_message("Este canal no esta vinculado. Usa `/link` primero.", ephemeral=True)

@tree.command(
    name="estado_limpieza",
    description="Muestra el estado de las limpiezas automáticas configuradas"
)
async def estado_limpieza(interaction: discord.Interaction):

    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return

    if not can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return

    guild_id = str(interaction.guild_id)

    if guild_id not in linked_channels or not linked_channels[guild_id]:

        embed = discord.Embed(
            title="🧹 Estado de Limpieza",
            description="No hay canales configurados para limpieza automática.",
            color=discord.Color.orange()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )
        return


    embed = discord.Embed(
        title="🧹 Estado de Limpieza",
        description=f"Servidor: **{interaction.guild.name}**",
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )


    current_time = datetime.now(timezone.utc)


    for channel_id, config in linked_channels[guild_id].items():

        channel = interaction.guild.get_channel(
            int(channel_id)
        )

        nombre_canal = (
            channel.mention
            if channel
            else f"Canal eliminado `{channel_id}`"
        )


        horas = config.get(
            "hours",
            0
        )


        try:

            last_clean = datetime.fromisoformat(
                config["last_clean"]
            )

            next_clean = (
                last_clean +
                timedelta(hours=horas)
            )


            if current_time >= next_clean:

                estado = (
                    "⚠️ Pendiente de limpieza"
                )

            else:

                restante = (
                    next_clean -
                    current_time
                )

                horas_restantes = (
                    restante.days * 24
                    +
                    restante.seconds // 3600
                )

                minutos_restantes = (
                    restante.seconds % 3600
                ) // 60


                estado = (
                    f"⏳ Próxima limpieza: "
                    f"**{horas_restantes}h "
                    f"{minutos_restantes}m**"
                )


            ultima = last_clean.strftime(
                "%d/%m/%Y %H:%M UTC"
            )


        except Exception:

            ultima = "Desconocida"
            estado = "❌ Error calculando estado"



        embed.add_field(
            name=f"📌 {nombre_canal}",
            value=(
                f"⏱ Intervalo: **{horas} horas**\n"
                f"🕒 Última limpieza:\n"
                f"`{ultima}`\n"
                f"{estado}"
            ),
            inline=False
        )


    embed.set_footer(
        text=f"Solicitado por {interaction.user}"
    )


    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

@tree.command(name="limpiar_ahora", description="Ejecuta una limpieza completa de este canal en este preciso instante")

async def limpiar_ahora(interaction: discord.Interaction):

    if not can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
    
    guild_id = str(interaction.guild_id)
    channel_id = str(interaction.channel_id)

    if interaction.guild_id is None:
        await interaction.response.send_message("Este comando solo puede usarse dentro de un servidor.", ephemeral=True)
        return

    # Sigue respetando que el servidor principal sea miembro de la whitelist
    if not is_whitelisted(interaction.guild_id):
        await interaction.response.send_message("Este servidor no esta autorizado.", ephemeral=True)
        return

    # 1. Respondemos de inmediato de forma efímera para evitar el timeout de 3 segundos de Discord
    await interaction.response.send_message("Iniciando limpieza inmediata en este canal...", ephemeral=True)
    
    # 2. Ejecutamos la purga completa de mensajes
    await purge_channel(interaction.channel)
    
    # 3. Verificamos si ESTE canal en específico tenía una limpieza automática programada con /link
    # Si estaba en la lista, reiniciamos su contador para que no vuelva a borrar pronto.
    # Si NO estaba en la lista, no pasa nada; se limpia en el momento y el bot no lo guardará para el auto-borrado.
    if guild_id in linked_channels and channel_id in linked_channels[guild_id]:
        linked_channels[guild_id][channel_id]["last_clean"] = datetime.now(timezone.utc).isoformat()
        save_linked_channels()
        print(f"Limpieza manual ejecutada. Se reinició el contador automático para el canal {channel_id}.")

# =========================
# TAREA AUTOMATICA DE LIMPIEZA (REVISIÓN CADA MINUTO)
# =========================
@tasks.loop(minutes=1.0)
async def auto_cleanER_task():
    current_time = datetime.now(timezone.utc)
    # Hacemos una copia para evitar errores de modificación de diccionarios en ejecución
    guilds_to_check = list(linked_channels.keys())

    for g_id in guilds_to_check:
        if g_id not in linked_channels: continue
        channels_to_check = list(linked_channels[g_id].keys())

        for c_id in channels_to_check:
            try:
                chan_config = linked_channels[g_id][c_id]
                last_clean_dt = datetime.fromisoformat(chan_config["last_clean"])
                hours_interval = chan_config["hours"]

                # Comprobar si ya pasó el tiempo necesario
                if current_time >= last_clean_dt + timedelta(hours=hours_interval):
                    channel = client.get_channel(int(c_id))
                    
                    # Si el bot no encuentra el canal en caché, intenta buscarlo en la API
                    if channel is None:
                        try:
                            channel = await client.fetch_channel(int(c_id))
                        except Exception:
                            channel = None

                    if isinstance(channel, discord.TextChannel):
                        print(f"Ejecutando limpieza automatica programada en el canal: {channel.name} ({c_id})")
                        await purge_channel(channel)
                        
                        # Actualizar tiempo de última limpieza exitosa
                        linked_channels[g_id][c_id]["last_clean"] = datetime.now(timezone.utc).isoformat()
                        save_linked_channels()
                    else:
                        # Si el canal ya no existe en el servidor, lo removemos del JSON para limpiar basura
                        print(f"El canal {c_id} parece no existir o fue borrado, removiendo de la lista.")
                        del linked_channels[g_id][c_id]
                        if not linked_channels[g_id]:
                            del linked_channels[g_id]
                        save_linked_channels()

            except Exception as e:
                print(f"Error procesando limpieza automatica en guild {g_id}, canal {c_id}: {e}")

# =========================
# ON READY / GUILD JOIN
# =========================
@client.event
async def on_ready():
    load_linked_channels()

    for guild in client.guilds:
        await leave_if_not_whitelisted(guild)

    await tree.sync()
    
    # Iniciar el bucle de limpieza automática si no está corriendo
    if not auto_cleanER_task.is_running():
        auto_cleanER_task.start()
        
    print(f"Conectado como {client.user}")

@client.event
async def on_guild_join(guild: discord.Guild):
    await leave_if_not_whitelisted(guild)

# =========================
# EJECUCION (TOKEN)
# =========================
if __name__ == "__main__":
    raw_token = os.getenv("DISCORD_TOKEN")
    TOKEN = raw_token.strip() if raw_token else None
    
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN no esta configurado.")
        exit(1)

    server_thread = Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()

    client.run(TOKEN)
