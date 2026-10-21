"""Catalog navigation for every product category; exact OEM matching stays conservative."""
import re

# The same catalog systems are exposed by Uniqom's original catalog UI.
CATALOG_SYSTEMS = (
    ('service', 'Детали для ТО', r'фильтр|свеч'),
    ('engine', 'Двигатель', r'двигател|грм|порш|клапан|проклад|ремень|ролик|натяж|сальник'),
    ('fuel', 'Топливная система', r'топлив|форсунк|бензонасос'),
    ('cooling', 'Система охлаждения', r'радиатор|термостат|помп|охлажд|водян'),
    ('exhaust', 'Система выпуска', r'глушител|кислород|лямбд|катализ|выхлоп'),
    ('transmission', 'Трансмиссия', r'сцеплен|шрус|привод|коробк|трансмис|маховик'),
    ('suspension', 'Ходовая часть', r'амортиз|подвес|рычаг|шаров|ступиц|стабилиз|подшип|пружин|сайлент'),
    ('steering', 'Рулевое управление', r'рулев|сошк|наконечник'),
    ('brakes', 'Тормозная система', r'тормоз|колодк|суппорт|абс|abs'),
    ('electrical', 'Электрооборудование', r'ламп|стартер|генератор|катушк|датчик|аккумулятор|электр|предохран|щетк'),
    ('climate', 'Отопление / кондиционирование', r'кондицион|отопител|компрессор|печк'),
    ('interior', 'Детали салона', r'коврик|салон|стеклоподъем|подлокот|ручк'),
    ('body', 'Детали кузова', r'бампер|капот|крыл|зеркал|кузов|стекл|двер|фара'),
    ('equipment', 'Дополнительное оборудование', r'.'),
)

# Exact names are matched in catalog rows, never guessed from a similar product name.
EXTRA_PARTS = {
    'brake_pad_front': (r'(?=.*колодк)(?=.*тормоз)(?=.*перед)', 'Brake system', ('pad kit, disc brake, front','front disc brake pad','front brake pad set')),
    'brake_pad_rear': (r'(?=.*колодк)(?=.*тормоз)(?=.*задн)', 'Brake system', ('pad kit, disc brake, rear','rear disc brake pad','rear brake pad set')),
    'brake_shoe': (r'колодк.*барабан|барабан.*колодк', 'Brake system', ('brake shoe set','shoe kit, rear brake','shoe kit, brake')),
    'brake_caliper': (r'суппорт.*тормоз|тормоз.*суппорт', 'Brake system', ('brake caliper','caliper assy','cylinder assy, disc brake')),
    'brake_hose': (r'шланг.*тормоз|тормоз.*шланг', 'Brake system', ('brake hose','hose, flexible')),
    'wheel_bearing': (r'подшипник.*ступиц|ступичн.*подшип', ('Suspension','Axles'), ('wheel bearing','bearing, front axle hub','bearing, rear axle')),
    'wheel_hub': (r'ступиц', ('Suspension','Axles'), ('hub & bearing assy','wheel hub','hub sub-assy')),
    'ball_joint': (r'шаров.*опор|опор.*шаров', 'Suspension', ('ball joint','joint assy, lower ball','joint assy, upper ball')),
    'stabilizer_link': (r'(?:стойк|тяг).*стабилиз|стабилиз.*(?:стойк|тяг)', 'Suspension', ('stabilizer link','link assy, stabilizer')),
    'shock_absorber': (r'амортизатор', 'Suspension', ('shock absorber','absorber assy, shock')),
    'coil_spring': (r'пружин.*подвес|пружин.*(?:перед|задн)', 'Suspension', ('coil spring','spring, coil')),
    'control_arm': (r'рычаг.*подвес|рычаг.*(?:нижн|верхн)', 'Suspension', ('control arm','arm sub-assy, suspension')),
    'water_pump': (r'помпа|насос.*водян|водян.*насос', 'Cooling system', ('water pump','pump assy, engine water')),
    'thermostat': (r'термостат', 'Cooling system', ('thermostat','thermostat assy')),
    'radiator': (r'радиатор.*(?:охлажд|двигател)|радиатор водян', 'Cooling system', ('radiator assy','engine radiator')),
    'ignition_coil': (r'катушк.*зажиган', 'Ignition system', ('ignition coil','coil assy, ignition')),
    'oxygen_sensor': (r'лямбда|датчик.*кислород', ('Exhaust system','Engine management'), ('oxygen sensor','sensor, oxygen')),
    'starter': (r'стартер', 'Electrical system', ('starter assy','starter motor')),
    'alternator': (r'генератор', 'Electrical system', ('alternator assy','alternator')),
    'clutch_disc': (r'диск.*сцеплен', 'Clutch', ('clutch disc','disc assy, clutch')),
    'clutch_cover': (r'корзин.*сцеплен', 'Clutch', ('clutch cover','cover assy, clutch')),
    'timing_belt': (r'ремень.*грм|грм.*ремень', 'Engine', ('timing belt','belt, timing')),
    'drive_belt': (r'ремень.*привод|ремень.*поликлин', 'Engine', ('drive belt','belt, v-ribbed')),
    'fuel_pump': (r'насос.*топлив|бензонасос', 'Fuel system', ('fuel pump','pump assy, fuel')),
    'fuel_injector': (r'форсунк.*топлив|форсунка.*двигател', 'Fuel system', ('fuel injector','injector assy, fuel')),
    'wiper_blade': (r'щетк.*стеклоочист|дворник', 'Body', ('wiper blade','blade, wiper')),
    'engine_mount': (r'опор.*двигател|подушк.*двигател', 'Engine', ('engine mounting','insulator, engine mounting')),
    'abs_sensor': (r'датчик.*(?:abs|абс)|(?:abs|абс).*датчик', 'Brake system', ('wheel speed sensor','sensor, speed')),
}


def category_plan(product, automatic_part=None):
    text = (product.name or '') + ' ' + (product.category or '')
    # More specific systems take precedence over generic words such as engine/filter.
    priorities = ('brakes','steering','cooling','exhaust','climate','fuel','suspension','transmission','electrical','service','engine','interior','body','equipment')
    systems = {key:(label,pattern) for key,label,pattern in CATALOG_SYSTEMS}
    group = next(key for key in priorities if re.search(systems[key][1],text,re.I))
    return {
        'all_categories': True,
        'automatic_supported': bool(automatic_part),
        'automatic_part': automatic_part or '',
        'group_id': group, 'group_label': systems[group][0],
        'category': product.category or product.name,
        'systems': [{'id':key,'label':label} for key,label,_ in CATALOG_SYSTEMS],
        'uniqom_mode':'browser',
        'uniqom_url':'https://uniqom.ru/original-catalogs',
    }

