from pathlib import Path

images_dir = Path('data/aic/images')
for f in images_dir.glob('*AIC_AIC*.jpg'):
    new_name = f.name
    while new_name.startswith('AIC_AIC'):
        new_name = new_name[4:]
    f.rename(f.parent / new_name)