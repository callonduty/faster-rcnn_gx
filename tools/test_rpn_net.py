#!/usr/bin/env python

# --------------------------------------------------------
# Faster R-CNN
# Copyright (c) 2015
# Licensed under The MIT License [see LICENSE for details]
# Written by 
# --------------------------------------------------------

"""Test a region proposal network on an image database."""

import _init_paths
from rpn.test import test_net
from rpn.test import im_detect
from rpn.config import cfg, cfg_from_file, get_output_dir
from datasets.factory import get_imdb
import datasets
import caffe
import argparse
import pprint
import time, os, sys, cv2
from utils.cython_nms import nms
from utils.timer import Timer
import matplotlib.pyplot as plt
import numpy as np
import cPickle
import heapq


# configuration
CONF_THRESH = 0.8
NMS_CONFIG = {'USE_GPU': False,
              'NMS_THRESH': 0.7,
              'PRE_NMS_TOPN': 6000,
              'POST_NMS_TOPN': 300}

def demo(net, image_name, anchor_file):
    # Load the demo image
    im_file = os.path.join(cfg.ROOT_DIR, 'data', 'demo', image_name)
    if not os.path.exists(im_file):
        print 'Image `{:s}` not found!'.format(image_name)
        return 
    im = cv2.imread(im_file)

    # Detect all object classes and regress object bounds
    timer = Timer()
    timer.tic()
    scores, boxes = im_detect(net, im, anchor_file)
    timer.toc()
    print ('Detection took {:.3f}s for '
           '{:d} object proposals').format(timer.total_time, boxes.shape[0])

    # Visualize detections for each class
    cls = 'obj'
    dets = np.hstack((boxes, scores)).astype(np.float32, copy=False)
    dets = boxes_filter(dets, 2000, # NMS_CONFIG['PRE_NMS_TOPN'], 
                              0.3,  # NMS_CONFIG['NMS_THRESH'], 
                              10 #NMS_CONFIG['POST_NMS_TOPN']
                        )
    CONF_THRESH = 0.99
    print 'All {} detections with p({} | box) >= {:.1f}'.format(cls, cls,
                                                                CONF_THRESH)
    # vis_detections(im, cls, dets, thresh=CONF_THRESH)
    res_im_file = os.path.join(cfg.ROOT_DIR, 'data', 'demo', 'res_'+image_name)
    save_detection_res(im, res_im_file, dets, CONF_THRESH)


def boxes_filter(dets, PRE_NMS_TOPN, NMS_THRESH, POST_NMS_TOPN, USE_GPU=False):
    """ filter the proposal boxes """
    # speed up nms 
    if PRE_NMS_TOPN > 0:
        dets = dets[: min(len(dets), PRE_NMS_TOPN)]
    
    # apply nms
    if NMS_THRESH > 0 and NMS_THRESH < 1:
        if USE_GPU:
            keep = nms_gpu(dets, NMS_THRESH)
        else:
            keep = nms(dets, NMS_THRESH)
        dets = dets[keep, :]

    if POST_NMS_TOPN > 0:
        dets = dets[: min(len(dets), POST_NMS_TOPN)]
    
    return dets


def vis_detections(im, class_name, dets, thresh=0.5):
    """Draw detected bounding boxes."""
    inds = np.where(dets[:, -1] >= thresh)[0]
    if len(inds) == 0:
        return

    im = im[:, :, (2, 1, 0)]
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.imshow(im, aspect='equal')
    for i in inds:
        bbox = dets[i, :4]
        score = dets[i, -1]

        ax.add_patch(
            plt.Rectangle((bbox[0], bbox[1]),
                          bbox[2] - bbox[0],
                          bbox[3] - bbox[1], fill=False,
                          edgecolor='red', linewidth=3.5)
            )
        ax.text(bbox[0], bbox[1] - 2,
                '{:s} {:.3f}'.format(class_name, score),
                bbox=dict(facecolor='blue', alpha=0.5),
                fontsize=14, color='white')

    ax.set_title(('{} detections with '
                  'p({} | box) >= {:.1f}').format(class_name, class_name,
                                                  thresh),
                  fontsize=14)
    plt.axis('off')
    plt.tight_layout()
    plt.draw()


def save_detection_res(im, path, dets, thresh=0.5):
    inds = np.where(dets[:, -1] >= thresh)[0]
    if len(inds) == 0:
        return
    
    font = cv2.FONT_HERSHEY_SIMPLEX
    boxes_size = []

    for i in inds:
        bbox = dets[i, :4]
        score = dets[i, -1]
        cv2.rectangle(im, (int(bbox[0]), int(bbox[1])), 
                    (int(bbox[2]), int(bbox[3])), 
                    (0, 255, 0), 2)
        cv2.putText(im, str(i), (int(bbox[0]), int(bbox[1])), font, 0.5, (0,0,255), 1)
        boxes_size.append([int(bbox[2]-bbox[0]), int(bbox[3]-bbox[1])])
    
    cv2.rectangle(im, (0,0), (300, (len(boxes_size)+1)*20), (125,125,125), -1)
    for i in inds:
        text_content = ('{:d}.scores: {:.3f}, box sixe: [{:d},{:d}]').format(i, 
                                dets[i, -1], boxes_size[i][0], boxes_size[i][1])
        cv2.putText(im, text_content, (0, (i+1)*20), font, 0.5, (255,0,0), 1)

    cv2.imwrite(path, im)


def imdb_append_proposals(net_def, caffemodel, imdb, anchors):
    """ Add proposal boxes which are generated by rpn """
    print 'Get proposal boxes and filter boxes...'
    pre_name = os.path.splitext(os.path.basename(caffemodel))[0] + '_on_' + imdb.name
    cache_file = os.path.join(cfg.ROOT_DIR, 'data', 'cache', pre_name + '_filtered_proposal_boxes.pkl')

    if os.path.exists(cache_file):
        with open(cache_file, 'rb') as f:
            box_list = cPickle.load(f)
        print 'load filtered boxes from \'{}\''.format(cache_file)
    else:
        caffe_net = caffe.Net(net_def, caffemodel, caffe.TEST)
        caffe_net.name = os.path.splitext(os.path.basename(caffemodel))[0]
    
        proposal_boxes = test_imdb(caffe_net, imdb, anchors)
        box_list = [ pbox[:,0:4] for pbox in proposal_boxes ]

        with open(cache_file, 'wb') as f:
            cPickle.dump(box_list, f, cPickle.HIGHEST_PROTOCOL)
    print 'Get proposal boxes and filter done!'
    
    print 'Appending proposal boxes to imdb...'
    proposal_roidb = imdb.create_roidb_from_box_list(box_list, imdb.roidb)
    imdb.roidb = datasets.imdb.merge_roidbs(imdb.roidb, proposal_roidb)
    print 'Append proposal boxes to imdb done!'

    return imdb


def test_imdb(net, imdb, anchors):
    """ Test a region proposal network on a image dataset  """
    print 'Generating proposal boxes by rpn model...'
    proposal_boxes = test_net(net, imdb, anchors)
    print 'Get proposal boxes done!'
    
    print 'Current NMS configuration:'
    print NMS_CONFIG

    # filter boxes
    print 'Filtering proposal boxes...'
    for i in xrange(len(proposal_boxes)):
        proposal_boxes[i] = boxes_filter(proposal_boxes[i], 
                PRE_NMS_TOPN=NMS_CONFIG['PRE_NMS_TOPN'], 
                NMS_THRESH=NMS_CONFIG['NMS_THRESH'], 
                POST_NMS_TOPN=NMS_CONFIG['POST_NMS_TOPN'],
                USE_GPU=NMS_CONFIG['USE_GPU'])
        print 'filter proposal box: {:d}/{:d}'.format(i, len(proposal_boxes))
    print 'Filter proposal boxes done!'
    
    return proposal_boxes


def test_imdb_comp(net, imdb, anchors):
    # generate proposal result boxes
    res_boxes = test_net(net, imdb, anchors)
    
    num_image = len(imdb.image_index)
    max_per_image = 100
    max_per_set = 40 * num_image
    thresh = -np.inf
    top_scores = []
    
    # get boxes with high score
    for i in xrange(num_image):
        dets = res_boxes[i]
        dets = dets[: min(len(dets), max_per_image)]
        res_boxes[i] = dets

        for det in dets:
            heapq.heappush(top_scores, det[-1])
        if len(top_scores) > max_per_set:
            while len(top_scores) > max_per_set:
                heapq.heappop(top_scores)
            thresh = top_scores[0]
        print 'filter boxes (top {:d}): {:d}/{:d}'.format(max_per_image, i, num_image)
        
    # conf thresh and nms
    for i in xrange(num_image):
        inds = np.where(res_boxes[i][:, -1] > thresh)[0]
        res_boxes[i] = res_boxes[i][inds, :] 
        res_boxes[i] = boxes_filter(res_boxes[i], -1, cfg.TEST.NMS, -1)
        print 'filter boxes (conf thresh & nms): {:d}/{:d}'.format(i, num_image)

    return res_boxes


def parse_args():
    """
    Parse input arguments
    """
    parser = argparse.ArgumentParser(description='Test a region proposal network')
    parser.add_argument('--gpu', dest='gpu_id', help='GPU id to use',
                        default=0, type=int)
    parser.add_argument('--cpu', dest='cpu_mode',
                        help='Use CPU mode (overrides --gpu)',
                        action='store_true')
    # parser.add_argument('--net', dest='demo_net', help='Network to use [rpn_vggm]',
    #                     choices=NETS.keys(), default='rpn_vggm')
    parser.add_argument('--def', dest='prototxt',
                        help='prototxt file defining the network',
                        default=None, type=str)
    parser.add_argument('--net', dest='caffemodel',
                        help='model to test',
                        default=None, type=str)
    parser.add_argument('--anchors', dest='anchors',
                        help='base anchor boxes',
                        default=None, type=str)
    parser.add_argument('--cfg', dest='cfg_file',
                        help='optional config file', default=None, type=str)
    parser.add_argument('--wait', dest='wait',
                        help='wait until net file exists',
                        default=True, type=bool)
    parser.add_argument('--imdb', dest='imdb_name',
                        help='dataset to test',
                        default=None, type=str)
    parser.add_argument('--comp', dest='comp_mode', help='competition mode',
                        action='store_true')

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    return args

if __name__ == '__main__':
    args = parse_args() 
    prototxt = args.prototxt
    caffemodel = args.caffemodel
    anchors = args.anchors

    print('Called with args:')
    print(args)

    if args.cfg_file is not None:
        cfg_from_file(args.cfg_file)

    print('Using config:')
    pprint.pprint(cfg)

    while not os.path.exists(caffemodel) and args.wait:
        print('Waiting for {} to exist...'.format(caffemodel))
        time.sleep(10)
    
    if args.cpu_mode:
        caffe.set_mode_cpu()
    else:
        caffe.set_mode_gpu()
        caffe.set_device(args.gpu_id)

    net = caffe.Net(prototxt, caffemodel, caffe.TEST)
    net.name = os.path.splitext(os.path.basename(caffemodel))[0]

    print '\n\nLoaded network {:s}'.format(caffemodel)
    
    if args.imdb_name:
        imdb = get_imdb(args.imdb_name)
        # res_boxes = test_imdb(net, imdb, anchors)
        res_boxes = test_imdb_comp(net, imdb, anchors)
        output_dir = get_output_dir(imdb, net)
        imdb.evaluate_detections(res_boxes, output_dir)
    else:
        # img_list = ['000012.jpg', '003681.png', '000008.png', '000010.png', '000013.png',
        #             '000004.jpg', '000018.png', '000022.png', '000047.png', '000056.png',
        #             '001111.jpg']
        img_list = ['1.jpg', '2.jpg', '3.jpg', '4.jpg', '5.jpg']
    
        for img_name in img_list:
            print '~' * 20
            print 'Demo for image: data/demo/' + img_name
            demo(net, img_name, anchors)

        plt.show()
