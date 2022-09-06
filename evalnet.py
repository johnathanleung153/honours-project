import os
import datetime
import argparse
import numpy as np
import tqdm
import pickle
from dotenv import load_dotenv

import torch
import torchvision.models as models
from torch.utils.data import DataLoader
from lib.utils import iter_quantisable_modules_with_names, process_img, get_images, CustomImageData, get_net, CONFIG_SETS, QuantisableModule, get_module,iter_trackable_modules, iter_trackable_modules_with_names
from lib.layer_tracker import HistogramTracker, Histogram
from lib.quantnet import test_quant

load_dotenv()

parser = argparse.ArgumentParser("quant-net")
#parser.add_argument("images_dir", help="images directory", type=str)
parser.add_argument("net_name", help="the net to test.", type=str)
#parser.add_argument("-l", "--labels-file", help="optional file with labels (use for image list)", type=str)

subparsers = parser.add_subparsers(dest='which') # store subcommand name in "which" field

parser_print_net = subparsers.add_parser('print-net')
parser_test = subparsers.add_parser('test-float')
parser_log = subparsers.add_parser('log-fixed')

parser_subset_fixed = subparsers.add_parser('test-subset-fixed')
parser_subset_fixed.add_argument('subset', type=str) #= subparsers.add_parser('test-subset-fixed')
parser_subset_fixed.add_argument('-i', '--ignore-existing', action=argparse.BooleanOptionalAction) #= subparsers.add_parser('test-subset-fixed')

parser_test_fixed = subparsers.add_parser('test-fixed')
parser_test_fixed.add_argument('quant_config', help='the quant config to use', type=str)
parser_test_fixed.add_argument('bounds', help='the bounds to use', type=str)
#parser.add_argument("type", help="which type", type=str, choices=["quant", "normal"])
args = parser.parse_args()

os.environ['CUDA_VISIBLE_DEVICES'] = '-1'

def test_accuracy(net: QuantisableModule, net_name: str, image_gen):
    loader = DataLoader(image_gen, batch_size=20)

    all_preds = []
    with torch.no_grad():
        for X in tqdm.tqdm(loader):
            preds = net.get_net()(X)
            # convert output to numpy
            preds_np = preds.cpu().detach().numpy()
            all_preds.append(preds_np)


    with open(f'output/floatpreds_{net_name}.npy', 'wb') as f:
        concated = np.concatenate(all_preds)
        print(concated.shape)
        np.save(f, concated)

def get_intermediate(net: QuantisableModule, net_name: str, image_gen):
    # Log all activations (outputs) of relevant layers
    # Note: these return names
    modules = list(iter_trackable_modules(net.get_net()))

    start_layer = modules[0]
    output_layers = modules

    hist_tracker = [HistogramTracker() for i in range(1 + len(output_layers))]

    def hist_tracker_output_hook(hist_tracker):
        def f(module, input, output):
            hist_tracker.update(output)
        return f
    def hist_tracker_input_hook(hist_tracker):
        def f(module, input, output):
            hist_tracker.update(input[0])
        return f

    # collect statistics of input (ie. first layer's input)
    start_layer.register_forward_hook(hist_tracker_input_hook(hist_tracker[0]))
    # collect statistics of activations
    for i, layer in enumerate(output_layers):
        layer.register_forward_hook(hist_tracker_output_hook(hist_tracker[i + 1]))
    
    loader = DataLoader(image_gen, batch_size=20)
    with torch.no_grad():
        for X in tqdm.tqdm(loader):
            preds = net.get_net()(X)

    histograms = [Histogram(tracker.range_pow_2, tracker.histogram.numpy()) for tracker in hist_tracker]

    with open(f"output/outputhistogram_{net_name}.pkl", "wb") as output_file:
        pickle.dump(histograms, output_file, protocol=pickle.HIGHEST_PROTOCOL)

def get_accuracy(label_file_name, file_name):
    with open(label_file_name) as label_f:
        labels = np.array([int(s.split()[1]) for s in label_f.readlines()])

    with open(file_name, 'rb') as f:
        quantpreds = np.load(f)

    #print(value_counts(quantpreds))

    quant_pred_labels = quantpreds.argmax(axis=1)

    #print(labels, quant_pred_labels)
    print("top 1 error:", np.mean(labels != quant_pred_labels))

def main(args):
    """ Test the network! """
    val_images_dir = os.getenv("VAL_IMAGES")
    calib_images_dir = os.getenv("CALIB_IMAGES")

    # train loop
    net = get_net(args.net_name)

    import torch

    if args.which == 'log-fixed':
        testing_files = get_images(calib_images_dir)
        image_gen = CustomImageData(testing_files)
        get_intermediate(net, args.net_name, image_gen)
    else:
        # contains a chosen subset of images and corresponding labels
        labels_file = os.getenv("VAL_SUBSET_LIST")
        # the list of image file names
        testing_files = get_images(val_images_dir, labels_file)
        image_gen = CustomImageData(testing_files)
        if args.which == 'test-float':
            test_accuracy(net, args.net_name, image_gen)
        elif args.which == 'test-fixed':
            test_quant(net, args.net_name, image_gen, args.quant_config, args.bounds, False) # in other module
        elif args.which == "test-subset-fixed":
            # test the neural net for all configurations
            for (quant_config, bounds) in CONFIG_SETS[args.net_name][args.subset]:
                print("Testing:", quant_config, bounds)
                test_quant(net, args.net_name, image_gen, quant_config, bounds, args.ignore_existing) # in other module
        elif args.which == "print-net":
            print(net.get_net())
            print("-- Layers to track:")
            for name, child in iter_trackable_modules_with_names(net.get_net()):
                print(name, type(child))
            print("-- Layers to quantise:")
            for name, child in iter_quantisable_modules_with_names(net.get_net()):
                print(name, type(child))
        else:
            print("No task selected.")

main(args)
