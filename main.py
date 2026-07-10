import os
import asyncio
from datetime import datetime, timedelta, timezone
from threading import Thread
import discord
from discord import app_commands
from discord.ext import tasks
from flask import Flask
from database import create_pool, SCHEMA_NAME

db = None

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
SUPPORT_DISCORD = os.getenv("SUPPORT_DISCORD", "denepoficial")
ACCESS_REQUEST_URL = os.getenv("ACCESS_REQUEST_URL", "https://discord.com/channels/@denepoficial")

async def is_whitelisted(guild_id: int | None):

    if guild_id is None:
        return False

    result = await db.fetchrow(
        f"""
        SELECT guild_id
        FROM {SCHEMA_NAME}.whitelist
        WHERE guild_id = $1
        """,
        guild_id
    )

    return result is not None

# =========================
# ROLES PERMITIDOS COMANDOS PRIVADOS
# =========================

async def get_allowed_roles(guild_id: int):

    rows = await db.fetch(
        f"""
        SELECT role_id
        FROM {SCHEMA_NAME}.allowed_roles
        WHERE guild_id = $1
        """,
        guild_id
    )

    return {
        row["role_id"]
        for row in rows
    }


async def add_allowed_role(guild_id: int, role_id: int):

    await db.execute(
        f"""
        INSERT INTO {SCHEMA_NAME}.allowed_roles
        (
            guild_id,
            role_id
        )
        VALUES
        (
            $1,
            $2
        )
        ON CONFLICT (guild_id, role_id)
        DO NOTHING
        """,
        guild_id,
        role_id
    )


async def remove_allowed_role(guild_id: int, role_id: int):

    await db.execute(
        f"""
        DELETE FROM {SCHEMA_NAME}.allowed_roles
        WHERE guild_id = $1
        AND role_id = $2
        """,
        guild_id,
        role_id
    )


async def has_allowed_role(member: discord.Member):

    allowed_roles = await get_allowed_roles(
        member.guild.id
    )

    user_roles = {
        role.id
        for role in member.roles
    }

    return bool(
        user_roles.intersection(
            allowed_roles
        )
    )


async def can_use_cleanup(member: discord.Member):

    if member.guild_permissions.administrator:
        return True

    return await has_allowed_role(member)
# =========================
# CONFIGURACION DEL BOT
# =========================
# NOTA: Para purgar/borrar mensajes antiguos, el bot NECESITA obligatoriamente 
# el intent de Message Content y permisos de Administrador o Gestionar Mensajes en Discord.
intents = discord.Intents.default()
intents.message_content = True 

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
db = None

# =========================
# CANALES VINCULADOS Y CONFIGURACIÓN
# =========================
async def get_linked_channel(guild_id: int, channel_id: int):
    row = await db.fetchrow(
        f"""
        SELECT guild_id, channel_id, hours, last_clean
        FROM {SCHEMA_NAME}.linked_channels
        WHERE guild_id = $1
        AND channel_id = $2
        """,
        guild_id,
        channel_id
    )

    return row


async def get_linked_channels_by_guild(guild_id: int):
    rows = await db.fetch(
        f"""
        SELECT guild_id, channel_id, hours, last_clean
        FROM {SCHEMA_NAME}.linked_channels
        WHERE guild_id = $1
        ORDER BY channel_id ASC
        """,
        guild_id
    )

    return rows


async def get_all_linked_channels():
    rows = await db.fetch(
        f"""
        SELECT guild_id, channel_id, hours, last_clean
        FROM {SCHEMA_NAME}.linked_channels
        ORDER BY guild_id ASC, channel_id ASC
        """
    )

    return rows


async def add_linked_channel(
    guild_id: int,
    channel_id: int,
    hours: int
):
    await db.execute(
        f"""
        INSERT INTO {SCHEMA_NAME}.linked_channels (
            guild_id,
            channel_id,
            hours,
            last_clean
        )
        VALUES (
            $1,
            $2,
            $3,
            $4
        )
        ON CONFLICT (guild_id, channel_id)
        DO NOTHING
        """,
        guild_id,
        channel_id,
        hours,
        datetime.now(timezone.utc)
    )


async def remove_linked_channel(
    guild_id: int,
    channel_id: int
):
    await db.execute(
        f"""
        DELETE FROM {SCHEMA_NAME}.linked_channels
        WHERE guild_id = $1
        AND channel_id = $2
        """,
        guild_id,
        channel_id
    )


async def update_linked_channel_hours(
    guild_id: int,
    channel_id: int,
    hours: int
):
    await db.execute(
        f"""
        UPDATE {SCHEMA_NAME}.linked_channels
        SET
            hours = $3,
            last_clean = $4
        WHERE guild_id = $1
        AND channel_id = $2
        """,
        guild_id,
        channel_id,
        hours,
        datetime.now(timezone.utc)
    )


async def update_linked_channel_last_clean(
    guild_id: int,
    channel_id: int
):
    await db.execute(
        f"""
        UPDATE {SCHEMA_NAME}.linked_channels
        SET last_clean = $3
        WHERE guild_id = $1
        AND channel_id = $2
        """,
        guild_id,
        channel_id,
        datetime.now(timezone.utc)
    )

# =========================
# CONFIGURACION DE LOGS
# =========================

async def get_logs_channel(guild_id: int):
    row = await db.fetchrow(
        f"""
        SELECT channel_id
        FROM {SCHEMA_NAME}.logs_channels
        WHERE guild_id = $1
        """,
        guild_id
    )

    if row is None:
        return None

    return row["channel_id"]


async def set_logs_channel(guild_id: int, channel_id: int):
    await db.execute(
        f"""
        INSERT INTO {SCHEMA_NAME}.logs_channels (
            guild_id,
            channel_id
        )
        VALUES (
            $1,
            $2
        )
        ON CONFLICT (guild_id)
        DO UPDATE SET
            channel_id = EXCLUDED.channel_id
        """,
        guild_id,
        channel_id
    )


async def send_log(
    guild: discord.Guild,
    embed: discord.Embed
):
    channel_id = await get_logs_channel(guild.id)

    if channel_id is None:
        return

    channel = guild.get_channel(channel_id)

    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except Exception:
            return

    try:
        await channel.send(embed=embed)

    except Exception as e:
        print(f"Error enviando log: {e}")


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
    if await is_whitelisted(guild.id): return
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

cleanup_lock = asyncio.Lock()


async def purge_channel(channel) -> int:
    """Borra todos los mensajes posibles de un canal."""

    async with cleanup_lock:

        if not channel.permissions_for(channel.guild.me).manage_messages:
            print(
                f"Falta permiso 'Gestionar Mensajes' en el canal {channel.name} ({channel.id})"
            )
            return 0

        deleted_count = 0

        try:
            deleted = await channel.purge(
                limit=None,
                bulk=True
            )

            deleted_count = len(deleted)

        except Exception as e:
            print(
                f"Error al purgar el canal {channel.id}: {e}"
            )

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
    if not interaction.user.guild_permissions.administrator:

        if not await can_use_cleanup(interaction.user):
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


    roles_actuales = await get_allowed_roles(
        interaction.guild.id
    )


    # Evitar duplicados
    if rol_id_int in roles_actuales:
        await interaction.response.send_message(
            f"⚠️ El rol `{rol_id_int}` ya está autorizado.",
            ephemeral=True
        )
        return


    # Guardar nuevo rol
    await add_allowed_role(
        interaction.guild.id,
        rol_id_int
    )


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
    if not await can_use_cleanup(interaction.user):
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


    roles_actuales = await get_allowed_roles(
        interaction.guild.id
    )


    # Verificar existencia
    if rol_id_int not in roles_actuales:
        await interaction.response.send_message(
            f"⚠️ El rol `{rol_id_int}` no está autorizado.",
            ephemeral=True
        )
        return


    # Quitar rol
    await remove_allowed_role(
        interaction.guild.id,
        rol_id_int
    )


    rol = interaction.guild.get_role(rol_id_int)


    nombre_rol = rol.name if rol else "Rol eliminado"


    await interaction.response.send_message(
        "✅ Rol eliminado correctamente.\n\n"
        f"🆔 ID: `{rol_id_int}`\n"
        f"🏷️ Nombre: `{nombre_rol}`",
        ephemeral=True
    )

@tree.command(
    name="listar_roles_limpieza",
    description="Muestra los roles autorizados para usar el sistema de limpieza"
)
async def listar_roles_limpieza(
    interaction: discord.Interaction
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return


    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para ver los roles autorizados.",
            ephemeral=True
        )
        return


    roles_autorizados = await get_allowed_roles(
        interaction.guild.id
    )


    if not roles_autorizados:

        embed = discord.Embed(
            title="🛡️ Roles autorizados de limpieza",
            description=(
                "No hay roles autorizados configurados actualmente."
            ),
            color=discord.Color.orange()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )
        return


    embed = discord.Embed(
        title="🛡️ Roles autorizados de limpieza",
        description=(
            "Roles que pueden usar los comandos privados del bot:"
        ),
        color=discord.Color.blue(),
        timestamp=datetime.now(timezone.utc)
    )


    for role_id in roles_autorizados:

        role = interaction.guild.get_role(
            role_id
        )


        if role:

            nombre = role.name

            valor = (
                f"ID: `{role.id}`\n"
                f"Mención: {role.mention}"
            )

        else:

            nombre = "⚠️ Rol eliminado"

            valor = (
                f"ID antiguo: `{role_id}`"
            )


        embed.add_field(
            name=f"🔹 {nombre}",
            value=valor,
            inline=False
        )


    embed.set_footer(
        text=f"Servidor: {interaction.guild.name}"
    )


    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

@tree.command(
    name="config_logs",
    description="Configura el canal privado donde se enviaran los logs"
)
@app_commands.describe(
    canal="Canal privado para recibir los logs"
)
async def config_logs(
    interaction: discord.Interaction,
    canal: discord.TextChannel
):

    if interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return


    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para configurar logs.",
            ephemeral=True
        )
        return


    await set_logs_channel(
        interaction.guild.id,
        canal.id
    )

    await send_log(
        interaction.guild,
        discord.Embed(
            title="📋 Sistema de Logs Activado",
            description=(
                f"El canal de logs fue configurado por {interaction.user.mention}\n"
                f"Canal: {canal.mention}"
            ),
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
    )

    embed = discord.Embed(
        title="📋 Logs configurados",
        description=(
            f"Canal de logs establecido correctamente:\n"
            f"{canal.mention}"
        ),
        color=discord.Color.green()
    )


    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

@tree.command(
    name="link",
    description="Vincula este canal para que se limpie automaticamente"
)
@app_commands.describe(
    horas="Cada cuantas horas se realizara la limpieza automatica (Por defecto: 24)"
)
async def link(interaction: discord.Interaction, horas: int = 24):

    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo puede usarse dentro de un servidor.",
            ephemeral=True
        )
        return

    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return

    if not isinstance(interaction.channel, (discord.TextChannel, discord.VoiceChannel)):
        await interaction.response.send_message(
            "Este comando solo funciona en canales con chat.",
            ephemeral=True
        )
        return

    if not await is_whitelisted(interaction.guild_id):
        await interaction.response.send_message(
            "Este servidor no esta autorizado para usar el bot.",
            ephemeral=True
        )
        return

    if horas <= 0:
        await interaction.response.send_message(
            "El intervalo de tiempo debe ser de al menos 1 hora.",
            ephemeral=True
        )
        return

    existing = await get_linked_channel(
        interaction.guild_id,
        interaction.channel_id
    )

    if existing is not None:
        await interaction.response.send_message(
            "Este canal ya se encuentra vinculado. Usa `/configurar` si quieres cambiar las horas.",
            ephemeral=True
        )
        return

    await add_linked_channel(
        interaction.guild_id,
        interaction.channel_id,
        horas
    )

    await send_log(
        interaction.guild,
        discord.Embed(
            title="🔗 Canal Vinculado",
            description=(
                f"Usuario: {interaction.user.mention}\n"
                f"Canal: {interaction.channel.mention}\n"
                f"Intervalo: **{horas} horas**"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
    )

    await interaction.response.send_message(
        f"Canal vinculado con exito. Este canal se limpiara por completo cada **{horas} horas**.",
        ephemeral=True
    )


@tree.command(
    name="unlink",
    description="Desvincula este canal del sistema de limpieza"
)
async def unlink(interaction: discord.Interaction):

    if interaction.guild_id is None or interaction.guild is None:
        await interaction.response.send_message(
            "Este comando solo puede usarse dentro de un servidor.",
            ephemeral=True
        )
        return

    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return

    existing = await get_linked_channel(
        interaction.guild_id,
        interaction.channel_id
    )

    if existing is None:
        await interaction.response.send_message(
            "Este canal no estaba vinculado.",
            ephemeral=True
        )
        return

    await remove_linked_channel(
        interaction.guild_id,
        interaction.channel_id
    )

    await send_log(
        interaction.guild,
        discord.Embed(
            title="🔓 Canal Desvinculado",
            description=(
                f"Usuario: {interaction.user.mention}\n"
                f"Canal: <#{interaction.channel_id}>"
            ),
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc)
        )
    )

    await interaction.response.send_message(
        "Canal desvinculado. Ya no se realizaran limpiezas automaticas aqui.",
        ephemeral=True
    )


@tree.command(
    name="configurar",
    description="Cambia el intervalo de horas de limpieza para este canal"
)
@app_commands.describe(
    horas="Nuevo intervalo de horas para la limpieza automatica"
)
async def configurar(interaction: discord.Interaction, horas: int):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Este comando solo puede usarse dentro de un servidor.",
            ephemeral=True
        )
        return
    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
    if horas <= 0:
        await interaction.response.send_message(
            "El intervalo debe ser de al menos 1 hora.",
            ephemeral=True
        )
        return
    existing = await get_linked_channel(
        interaction.guild_id,
        interaction.channel_id
    )
    if existing is None:
        await interaction.response.send_message(
            "Este canal no esta vinculado. Usa `/link` primero.",
            ephemeral=True
        )
        return
    await update_linked_channel_hours(
        interaction.guild_id,
        interaction.channel_id,
        horas
    )
    await interaction.response.send_message(
        f"Configuracion actualizada. Ahora este canal se limpiara cada **{horas} horas**.",
        ephemeral=True
    )

@tree.command(
    name="estado_limpieza",
    description="Muestra el estado de las limpiezas automáticas configuradas"
)
async def estado_limpieza(interaction: discord.Interaction):

    if interaction.guild is None or interaction.guild_id is None:
        await interaction.response.send_message(
            "Este comando solo funciona dentro de servidores.",
            ephemeral=True
        )
        return

    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return

    rows = await get_linked_channels_by_guild(
        interaction.guild_id
    )

    if not rows:
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

    for row in rows:
        channel_id = row["channel_id"]
        horas = row["hours"]
        last_clean = row["last_clean"]

        channel = interaction.guild.get_channel(channel_id)

        nombre_canal = (
            channel.mention
            if channel
            else f"Canal eliminado `{channel_id}`"
        )

        try:
            if last_clean.tzinfo is None:
                last_clean = last_clean.replace(tzinfo=timezone.utc)

            next_clean = last_clean + timedelta(hours=horas)

            if current_time >= next_clean:
                estado = "⚠️ Pendiente de limpieza"
            else:
                restante = next_clean - current_time

                horas_restantes = (
                    restante.days * 24 +
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

    if not await can_use_cleanup(interaction.user):
        await interaction.response.send_message(
            "❌ No tienes permisos para usar este comando.",
            ephemeral=True
        )
        return
    

    if interaction.guild_id is None:
        await interaction.response.send_message("Este comando solo puede usarse dentro de un servidor.", ephemeral=True)
        return

    # Sigue respetando que el servidor principal sea miembro de la whitelist
    if not await is_whitelisted(interaction.guild_id):
        await interaction.response.send_message("Este servidor no esta autorizado.", ephemeral=True)
        return

    # 1. Respondemos de inmediato de forma efímera para evitar el timeout de 3 segundos de Discord
    await interaction.response.send_message("Iniciando limpieza inmediata en este canal...", ephemeral=True)
    
    # 2. Ejecutamos la purga completa de mensajes
    deleted = await purge_channel(interaction.channel)

    await send_log(
        interaction.guild,
        discord.Embed(
            title="🧹 Limpieza Manual Ejecutada",
            description=(
                f"Usuario: {interaction.user.mention}\n"
                f"Canal: {interaction.channel.mention}\n"
                f"Mensajes eliminados: **{deleted}**"
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
    )
    
    # 3. Verificamos si ESTE canal en específico tenía una limpieza automática programada con /link
    # Si estaba en la lista, reiniciamos su contador para que no vuelva a borrar pronto.
    # Si NO estaba en la lista, no pasa nada; se limpia en el momento y el bot no lo guardará para el auto-borrado.
   

    if deleted > 0:
        existing = await get_linked_channel(
            interaction.guild_id,
            interaction.channel_id
        )

        if existing is not None:
            await update_linked_channel_last_clean(
                interaction.guild_id,
                interaction.channel_id
            )

            print(
                f"Limpieza manual ejecutada. "
                f"Contador reiniciado para el canal {interaction.channel_id}."
            )


# =========================
# TAREA AUTOMATICA DE LIMPIEZA (REVISIÓN CADA MINUTO)
# =========================
@tasks.loop(minutes=1.0)
async def auto_cleanER_task():
    current_time = datetime.now(timezone.utc)

    rows = await get_all_linked_channels()

    for row in rows:
        guild_id = row["guild_id"]
        channel_id = row["channel_id"]
        hours_interval = row["hours"]
        last_clean_dt = row["last_clean"]

        try:
            if last_clean_dt.tzinfo is None:
                last_clean_dt = last_clean_dt.replace(tzinfo=timezone.utc)

            if current_time < last_clean_dt + timedelta(hours=hours_interval):
                continue

            channel = client.get_channel(channel_id)

            if channel is None:
                try:
                    channel = await client.fetch_channel(channel_id)
                except Exception:
                    channel = None

            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                print(
                    f"Ejecutando limpieza automatica programada en el canal: "
                    f"{channel.name} ({channel_id})"
                )

                deleted = await purge_channel(channel)

                await send_log(
                    channel.guild,
                    discord.Embed(
                        title="🧹 Limpieza Automática Ejecutada",
                        description=(
                            f"Canal limpiado: {channel.mention}\n"
                            f"Mensajes eliminados: **{deleted}**"
                        ),
                        color=discord.Color.red(),
                        timestamp=datetime.now(timezone.utc)
                    )
                )

                await update_linked_channel_last_clean(
                    guild_id,
                    channel_id
                )

            else:
                print(
                    f"El canal {channel_id} parece no existir o fue borrado, "
                    f"removiendo de la base de datos."
                )

                await remove_linked_channel(
                    guild_id,
                    channel_id
                )

        except Exception as e:
            print(
                f"Error procesando limpieza automatica en guild {guild_id}, "
                f"canal {channel_id}: {e}"
            )


# =========================
# ON READY / GUILD JOIN
# =========================
@client.event
async def on_ready():

    global cleanup_lock

    @client.event
    async def on_ready():

    global cleanup_lock
        global db

        if db is None:
            db = await create_pool()
            print("Base de datos conectada")

        for guild in client.guilds:
            await leave_if_not_whitelisted(guild)

        await tree.sync()

        if not auto_cleanER_task.is_running():
            auto_cleanER_task.start()

        print(f"Conectado como {client.user}")


    global db

    if db is None:
        db = await create_pool()
        print("Base de datos conectada")
    
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
