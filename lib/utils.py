import os
import argparse
import numpy as np
import torch
import PIL
from typing import List, Optional
import torchvision
from torchvision import transforms as T
from torchvision.transforms import InterpolationMode

from lib.models import QuantisableModule, QuantizableConvRelu

normalize = T.Normalize(mean=[0.485, 0.456, 0.406],
                                 std=[0.229, 0.224, 0.225])

process_img = T.Compose([T.Resize(256, interpolation=InterpolationMode.BICUBIC), T.CenterCrop(224), T.ToTensor(), normalize])


# Utility to set network to eval mode.
def set_eval(net):
    net.eval()
    return net

# ====
def iter_trackable_modules(module: torch.nn.Module):
    for _, child in iter_trackable_modules_helper_(module, None):
        yield child

def iter_trackable_modules_with_names(module: torch.nn.Module):
    yield from iter_trackable_modules_helper_(module, None)

def iter_trackable_modules_helper_(module: torch.nn.Module, parent_name: Optional[str]):
    """ Iterate moduls recursively """
    # These are leaves that are sequential modules but we want to ignore
    # the "content" inside, because they are assumed to be one operation in the quantised version.
    seq_leaf_types = [QuantizableConvRelu, torch.nn.intrinsic.modules.ConvReLU2d, torch.nn.intrinsic.modules.LinearReLU]

    ignore = [torch.nn.Identity, torch.nn.Dropout, torch.nn.MaxPool2d, torch.nn.AdaptiveAvgPool2d]
    for name, child in module.named_children():
        full_name = name if parent_name is None else f"{parent_name}.{name}"
        # not a leaf; ignore it
        if not type(child) in seq_leaf_types:
            yield from iter_trackable_modules_helper_(child, full_name)

        # is it a leaf?
        if (len(child._modules) == 0 or type(child) in seq_leaf_types) and type(child) not in ignore:
            yield (full_name, child)

def iter_quantisable_modules_with_names(module: torch.nn.Module):
    yield from iter_quantisable_modules_helper_(module, None)

def iter_quantisable_modules_helper_(module: torch.nn.Module, parent_name: Optional[str]):
    """ Iterate moduls recursively """
    leaves = [torch.nn.Conv2d, torch.nn.Linear]
    for name, child in module.named_children():
        full_name = name if parent_name is None else f"{parent_name}.{name}"
        yield from iter_quantisable_modules_helper_(child, full_name)
            
        if type(child) in leaves:
            yield (full_name, child)

# Access a nested object by dot notation (for example, relu.0)
# Based on code in pytorch/pytorch
def get_module(model, submodule_key):
    tokens = submodule_key.split('.')
    cur_mod = model
    for s in tokens:
        cur_mod = getattr(cur_mod, s)
    return cur_mod

def set_module(model, submodule_key, module):
    tokens = submodule_key.split('.')
    sub_tokens = tokens[:-1]
    cur_mod = model
    for s in sub_tokens:
        cur_mod = getattr(cur_mod, s)

    #print("mod:", cur_mod)
    if not hasattr(cur_mod, tokens[-1]):
        raise RuntimeError(f"attr does not exist: {cur_mod}, {tokens[-1]}")
    setattr(cur_mod, tokens[-1], module)

def run_net(net: QuantisableModule, loader: torch.utils.data.DataLoader, device: str):
    """ Run net, get predictions. """
    all_preds = []
    labels = []

    import tqdm
    # evaluate network
    with torch.no_grad():
        net.get_net().to(device)
        for X, label in tqdm.tqdm(loader):
            preds = net.get_net()(X.to(device))
            # convert output to numpy
            preds_np = preds.cpu().detach().numpy()
            all_preds.append(preds_np)
            labels.append(label)
    
    return np.concatenate(all_preds), np.concatenate(labels)

def get_readable_layer_names(net_name, layer_names):
    if net_name == 'vgg11':
        return [f'features {i}' for i in range(1,9)] + [f'classifier {i}' for i in [1,2,3]]
    else:
        layer_names = [n.replace(".", " ").replace("conv_relu1 ", "") for n in layer_names]
        return layer_names

# ========
class CustomImageData(torch.utils.data.Dataset):
    
    def __init__(self, images_metadata: List[tuple[str, int]]):
        self.images_metadata = images_metadata
        self.len = len(images_metadata)
    
    def __getitem__(self, index):
        file_path, label = self.images_metadata[index]
        img = PIL.Image.open(file_path).convert('RGB')
        img = process_img(img)
        return img, label
    
    def __len__(self):
        return self.len

# Get image path to tune the classifier quantiser params.
def get_images(images_dir, labels_file: str) -> CustomImageData:
    with open(labels_file, "r") as f:
        lines = f.readlines()
        images_metadata = []
        for l in lines:
            file_name, label = l.split()
            images_metadata.append((os.path.join(images_dir, file_name), int(label)))
    # sort by filenmae only. Important for labels compatibility
    images_metadata.sort(key=lambda f: f[0].split("/")[-1])

    # first is validation; second is testing
    return CustomImageData(images_metadata)
