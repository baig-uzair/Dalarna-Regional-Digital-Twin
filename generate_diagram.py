import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyArrowPatch

fig, ax = plt.subplots(figsize=(18, 10))
ax.set_xlim(0, 100)
ax.set_ylim(0, 100)
ax.axis('off')

bg = '#f4f6f8'
panel = '#edf3f8'
frame = '#2a3f57'
source = '#f7f7f7'
adapter = '#dfeefb'
core = '#eaf7e9'
store = '#f9ebd7'
vis = '#f0e7f9'
text = '#1e2d3d'
line = '#2a3f57'

fig.patch.set_facecolor(bg)
ax.set_facecolor(bg)

panel_box = Rectangle((8, 8), 84, 78, facecolor=panel, edgecolor=frame, linewidth=1.5)
ax.add_patch(panel_box)
ax.text(50, 90, 'Digital Twin Architecture', ha='center', va='center', fontsize=28, fontweight='bold', color=text)

src_x, src_y = 10, 70
src_w, src_h = 16, 10
srcs = [
    ('SMHI', 'Air / Weather'),
    ('Trafikverket', 'Road / Traffic'),
    ('SCB', 'Socioeconomic'),
    ('SGU', 'Geology'),
    ('Skogsstyrelsen', 'Forest'),
    ('Lantmateriet', 'Land / Maps'),
]
for i, (name, label) in enumerate(srcs):
    x = src_x + i * 13.5
    box = Rectangle((x, src_y), src_w, src_h, facecolor=source, edgecolor=frame, linewidth=1.2)
    ax.add_patch(box)
    ax.text(x + src_w/2, src_y + src_h/2 + 1.7, name, ha='center', va='center', fontsize=10, fontweight='bold', color=text)
    ax.text(x + src_w/2, src_y + src_h/2 - 1.7, label, ha='center', va='center', fontsize=8, color=text)

adapter_box = Rectangle((8, 45), 84, 28, facecolor='white', edgecolor=frame, linewidth=1.4)
ax.add_patch(adapter_box)
ax.text(50, 72, 'Adapter Layer', ha='center', va='center', fontsize=16, fontweight='bold', color=text)

adapters = [
    ('adapter-smhi', 15, 56),
    ('adapter-trafikverket', 30, 56),
    ('adapter-scb', 45, 56),
    ('adapter-sgu', 60, 56),
    ('adapter-skogsstyrelsen', 75, 56),
    ('adapter-lantmateriet', 90, 56),
]
for name, x, y in adapters:
    if x > 85:
        x = 85
    box = Rectangle((x-6, y-4.5), 12, 9, facecolor=adapter, edgecolor=frame, linewidth=1.1)
    ax.add_patch(box)
    ax.text(x, y, name, ha='center', va='center', fontsize=8, color=text)

core_y = 25
broker = Rectangle((26, core_y), 22, 13, facecolor=core, edgecolor=frame, linewidth=1.3)
ax.add_patch(broker)
ax.text(37, core_y + 6.5, 'NGSI-LD Context Broker\n(Orion)', ha='center', va='center', fontsize=11, color=text)

store = Rectangle((52, core_y), 16, 13, facecolor=store, edgecolor=frame, linewidth=1.3)
ax.add_patch(store)
ax.text(60, core_y + 6.5, 'Time-Series\nStore', ha='center', va='center', fontsize=11, color=text)

analytics = Rectangle((71, core_y), 15, 13, facecolor=core, edgecolor=frame, linewidth=1.3)
ax.add_patch(analytics)
ax.text(78.5, core_y + 6.5, 'Analytics &\nAlerts', ha='center', va='center', fontsize=11, color=text)

panel = Rectangle((54, 8), 26, 12, facecolor=vis, edgecolor=frame, linewidth=1.3)
ax.add_patch(panel)
ax.text(67, 15, 'Interactive Web Interface\nDashboard / Map / Controls', ha='center', va='center', fontsize=11, color=text)

proc = Rectangle((13, 22), 18, 12, facecolor='#e6f5eb', edgecolor=frame, linewidth=1.3)
ax.add_patch(proc)
ax.text(22, 28, 'Scheduler\nJob Executor', ha='center', va='center', fontsize=11, fontweight='bold', color=text)

for i, (name, label) in enumerate(srcs):
    x = 10 + i * 13.5 + 8
    arr = FancyArrowPatch((x, 70), (x, 65), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
    ax.add_patch(arr)

for x in [15, 30, 45, 60, 75, 85]:
    arr = FancyArrowPatch((x, 56), (x - 5, 34), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
    ax.add_patch(arr)

arr1 = FancyArrowPatch((31, 26), (36, 27), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
arr2 = FancyArrowPatch((31, 26), (52, 27), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
arr3 = FancyArrowPatch((31, 26), (71, 27), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
ax.add_patch(arr1)
ax.add_patch(arr2)
ax.add_patch(arr3)

arr4 = FancyArrowPatch((68, 16), (54, 16), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
arr5 = FancyArrowPatch((49, 16), (54, 16), arrowstyle='-|>', mutation_scale=12, linewidth=1.2, color=line)
ax.add_patch(arr4)
ax.add_patch(arr5)

output = 'architecture_diagram.png'
plt.tight_layout()
plt.savefig(output, dpi=220, bbox_inches='tight', facecolor=fig.get_facecolor())
print(f'Created: {output}')
